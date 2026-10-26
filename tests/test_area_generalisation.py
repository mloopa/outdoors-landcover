"""Low-zoom area generalisation: dissolve, gap closing, thin-part removal, hole filling."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box

from outdoors_landcover.generalise import generalise_area_gdf
from outdoors_landcover.mapping.loader import GeneralisationConfig, GeneralisationProfile

# Web Mercator coordinates near 52°N so distances/areas are in profile units.
X0, Y0 = 2_100_000.0, 6_800_000.0


def _config() -> GeneralisationConfig:
    return GeneralisationConfig(
        version=1,
        crs_for_area="EPSG:3857",
        output_crs="EPSG:3857",
        profiles={},
        layer_profiles={},
        tile_layer_names={},
    )


def _profile(**overrides) -> GeneralisationProfile:
    params = dict(
        name="test",
        description="",
        min_area_m2=1_000_000,
        simplify_tolerance_m=10,
        dissolve_same_attributes=False,
        repair=True,
        dissolve_by=[],
        close_gap_m=150,
        open_width_m=100,
        fill_holes_below_m2=1_000_000,
        tile_size_m=50_000,
    )
    params.update(overrides)
    return GeneralisationProfile(**params)


def _sq(x: float, y: float, size: float) -> Polygon:
    return box(X0 + x, Y0 + y, X0 + x + size, Y0 + y + size)


def _run(geoms, profile=None, **cols) -> gpd.GeoDataFrame:
    profile = profile or _profile()
    gdf = gpd.GeoDataFrame({"geometry": geoms, **cols}, crs="EPSG:3857")
    return generalise_area_gdf(
        gdf, profile, _config(), group_cols=profile.dissolve_by, max_workers=1
    )


def test_close_gap_merges_neighbours():
    out = _run([_sq(0, 0, 2000), _sq(2100, 0, 2000)])
    assert len(out) == 1
    assert out.geometry.iloc[0].area > 2 * 2000 * 2000


def test_wide_gap_kept():
    out = _run([_sq(0, 0, 2000), _sq(3000, 0, 2000)])
    assert len(out) == 2


def test_tiny_isolated_polygons_dropped():
    out = _run([_sq(0, 0, 3000), _sq(10_000, 0, 600)])
    assert len(out) == 1
    assert out.geometry.iloc[0].bounds[0] == pytest.approx(X0, abs=1)


def test_small_holes_filled_large_holes_kept():
    outer = _sq(0, 0, 8000)
    small_hole = _sq(1000, 1000, 500).exterior.coords
    large_hole = _sq(4000, 4000, 2500).exterior.coords
    out = _run([Polygon(outer.exterior.coords, [small_hole, large_hole])])
    assert len(out) == 1
    assert len(out.geometry.iloc[0].interiors) == 1


def test_thin_strips_removed():
    strip = box(X0, Y0, X0 + 20_000, Y0 + 150)
    out = _run([strip], _profile(close_gap_m=0, min_area_m2=0))
    assert out.empty


def test_tile_seams_do_not_split_polygons():
    big = _sq(0, 0, 9000)
    out = _run([big], _profile(tile_size_m=2000))
    assert len(out) == 1
    geom = out.geometry.iloc[0]
    assert not geom.interiors
    assert geom.area == pytest.approx(big.area, rel=0.01)


def test_dissolve_groups_and_drops_other_attributes():
    out = _run(
        [_sq(0, 0, 2000), _sq(2100, 0, 2000)],
        _profile(dissolve_by=["landcover"]),
        landcover=["crop", "grass"],
        name=["a", "b"],
    )
    assert sorted(out["landcover"]) == ["crop", "grass"]
    assert "name" not in out.columns
    assert out["id"].is_unique
