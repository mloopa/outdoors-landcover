"""Geometry validation and generalisation behaviour."""

from __future__ import annotations

import geopandas as gpd
from helpers import CONFIG
from shapely.geometry import Polygon

from outdoors_landcover.generalise import generalise_gdf
from outdoors_landcover.mapping.loader import load_generalisation
from outdoors_landcover.transform.geometry import (
    area_km2_geodesic,
    repair_geometry,
    stable_feature_id,
)


def test_repair_bowtie():
    # Self-intersecting bowtie
    geom = Polygon([(0, 0), (1, 1), (0, 1), (1, 0), (0, 0)])
    fixed = repair_geometry(geom)
    assert fixed is not None
    assert not fixed.is_empty


def test_area_km2_positive():
    # ~1 degree square near equator ≈ 12,000+ km²
    geom = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    assert area_km2_geodesic(geom) > 1000


def test_stable_id_deterministic():
    g = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    a = stable_feature_id("1", "woodland", g)
    b = stable_feature_id("1", "woodland", g)
    assert a == b


def test_tiny_polygon_removed():
    cfg = load_generalisation(CONFIG / "generalisation.yaml")
    profile = cfg.profiles["z0_9"]
    tiny = Polygon(
        [
            (-9.0, 53.0),
            (-9.0 + 1e-5, 53.0),
            (-9.0 + 1e-5, 53.0 + 1e-5),
            (-9.0, 53.0 + 1e-5),
            (-9.0, 53.0),
        ]
    )
    gdf = gpd.GeoDataFrame(
        [{"id": "a", "landcover": "crop", "geometry": tiny}],
        crs="EPSG:4326",
    )
    out = generalise_gdf(gdf, profile, cfg, attribute_cols=["landcover"])
    assert out.empty
