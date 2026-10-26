"""CLMS High Resolution Layer mapping, merge, and local ingest."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from helpers import CONFIG
from rasterio.transform import from_origin
from shapely.geometry import Polygon

from outdoors_landcover.config import Settings
from outdoors_landcover.countries import is_europe_coverage, load_countries, resolve_scope
from outdoors_landcover.mapping.clms import attributes_for_value, bbox_for_scope, load_clms_config
from outdoors_landcover.mapping.loader import load_generalisation
from outdoors_landcover.merge import clms_has_better_resolution, select_source_rows
from outdoors_landcover.pipeline import run_pipeline


def _clms():
    return load_clms_config(CONFIG / "clms.yaml")


def test_clms_config_is_hrl_not_clc():
    cfg = _clms()
    assert "clc" not in cfg.layers["woodland"].dataset_identifier
    assert "forest-type" in cfg.layers["woodland"].dataset_identifier
    assert cfg.layers["grass"].dataset_identifier.startswith("clms_vlcc_grassland")
    assert cfg.layers["crop"].dataset_identifier.startswith("clms_vlcc_crop-types")
    assert cfg.native_resolution_m == 10


def test_fty_and_gra_class_values():
    cfg = _clms()
    wood = cfg.layers["woodland"]
    grass = cfg.layers["grass"]
    crop = cfg.layers["crop"]
    assert attributes_for_value(wood, 0) is None
    assert attributes_for_value(wood, 1) == {"forest_type": "non_coniferous"}
    assert attributes_for_value(wood, 2) == {"forest_type": "coniferous"}
    assert attributes_for_value(grass, 1) == {"landcover": "grass"}
    assert attributes_for_value(crop, 1110) == {"landcover": "crop"}
    assert attributes_for_value(crop, 2100) == {"landcover": "crop"}
    assert attributes_for_value(crop, 0) is None


def test_europe_bbox_and_country():
    cfg = _clms()
    catalog = load_countries(CONFIG / "countries.yaml")
    _label, extracts, resolved = resolve_scope("country", "PL", catalog)
    assert is_europe_coverage(extracts)
    assert bbox_for_scope(cfg, iso=resolved.iso, extract_keys=extracts) == cfg.country_bboxes["PL"]
    _label, global_extracts, _c = resolve_scope("global", None, catalog)
    assert bbox_for_scope(cfg, iso=None, extract_keys=global_extracts) == cfg.europe_bbox
    us = catalog.countries["US"]
    assert bbox_for_scope(cfg, iso="US", extract_keys=us.extracts) is None


def test_resolution_gate_prefers_clms_at_low_zoom():
    gen = load_generalisation(CONFIG / "generalisation.yaml")
    assert clms_has_better_resolution(gen.profiles["z0_9"], native_resolution_m=10)
    assert clms_has_better_resolution(gen.profiles["z8_9"], native_resolution_m=10)
    assert clms_has_better_resolution(gen.profiles["z11_14"], native_resolution_m=10)
    assert not clms_has_better_resolution(gen.profiles["z14_plus"], native_resolution_m=10)
    assert not clms_has_better_resolution(gen.profiles["z11_plus"], native_resolution_m=10)

    osm = [{"id": "osm", "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])}]
    clms = [{"id": "clms", "geometry": Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])}]
    rows, src = select_source_rows(
        logical="crop",
        profile=gen.profiles["z0_9"],
        osm_rows=osm,
        clms_rows=clms,
        native_resolution_m=10,
    )
    assert src == "clms" and rows is clms
    rows, src = select_source_rows(
        logical="woodland",
        profile=gen.profiles["z14_plus"],
        osm_rows=osm,
        clms_rows=clms,
        native_resolution_m=10,
    )
    assert src == "osm" and rows is osm


def _write_class_tif(path: Path, value: int, dtype="uint8") -> Path:
    import rasterio

    arr = np.zeros((20, 20), dtype=dtype)
    arr[2:18, 2:18] = value
    transform = from_origin(-9.25, 53.20, 0.01, 0.01)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype=arr.dtype,
        crs="EPSG:4326",
        transform=transform,
        nodata=0,
    ) as dst:
        dst.write(arr, 1)
    return path


def test_pipeline_uses_local_clms_for_europe(tmp_path: Path):
    from helpers import FIXTURES

    clms_dir = tmp_path / "clms"
    clms_dir.mkdir()
    _write_class_tif(clms_dir / "woodland.tif", 2)
    _write_class_tif(clms_dir / "grass.tif", 1)
    _write_class_tif(clms_dir / "crop.tif", 1110, dtype="uint16")

    out = tmp_path / "out"
    settings = Settings(
        output_dir=out,
        cache_dir=out / "cache",
        temp_dir=out / "tmp",
        concurrency=2,
        clms_enabled=True,
        clms_local_root=clms_dir,
    )
    result = run_pipeline(
        scope="country",
        country="IE",
        settings=settings,
        local_root=FIXTURES,
        offline=True,
    )
    assert result.metadata_path is not None
    import json

    meta = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert meta["clms"]["service"] == "Copernicus Land Monitoring Service"
    assert "CLC" in meta["clms"]["not"]
    crop_out = next(o for o in meta["outputs"] if o["logical"] == "crop")
    wood_mid = next(
        o for o in meta["outputs"] if o["logical"] == "woodland" and o["profile"] == "z8_9"
    )
    wood_hi = next(
        o for o in meta["outputs"] if o["logical"] == "woodland" and o["profile"] == "z14_plus"
    )
    beach = next(o for o in meta["outputs"] if o["logical"] == "beach")
    assert crop_out["source"] == "clms"
    assert wood_mid["source"] == "clms"
    assert wood_hi["source"] == "osm"
    assert beach["source"] == "osm"
    assert crop_out["feature_count"] >= 1
    attr = (out / "IE__ATTRIBUTION.txt").read_text(encoding="utf-8")
    assert "OpenStreetMap" in attr
    assert "Copernicus Land Monitoring Service" in attr


def test_offline_without_clms_rasters_falls_back_to_osm(tmp_path: Path):
    from helpers import FIXTURES

    settings = Settings(
        output_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache",
        temp_dir=tmp_path / "tmp",
        concurrency=1,
        clms_enabled=True,
    )
    result = run_pipeline(
        scope="country",
        country="IE",
        settings=settings,
        local_root=FIXTURES,
        offline=True,
    )
    assert result.stats["accepted"] >= 8
