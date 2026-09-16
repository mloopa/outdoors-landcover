"""Integration test using the checked-in OSM fixture (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest
from helpers import FIXTURES
from typer.testing import CliRunner

from outdoors_landcover.cli import app
from outdoors_landcover.config import Settings
from outdoors_landcover.pipeline import run_pipeline


@pytest.fixture()
def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "out"


def test_integration_ireland_fixture(out_dir: Path):
    settings = Settings(
        output_dir=out_dir,
        cache_dir=out_dir / "cache",
        temp_dir=out_dir / "tmp",
        concurrency=1,
    )
    result = run_pipeline(
        scope="country",
        country="IE",
        settings=settings,
        local_root=FIXTURES,
        offline=True,
    )
    assert not result.dry_run
    assert result.metadata_path is not None
    meta = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert meta["source"]["attribution"].startswith("© OpenStreetMap")
    assert meta["counts"]["accepted"] >= 8

    # Expected layer files
    beach = out_dir / "IE__beach__z11_plus.gpkg"
    crop = out_dir / "IE__crop__z0_9.gpkg"
    grass = out_dir / "IE__grass__z0_9.gpkg"
    wood = out_dir / "IE__woodland__z14_plus.gpkg"
    assert beach.is_file()
    assert crop.is_file()
    assert grass.is_file()
    assert wood.is_file()
    assert (out_dir / "IE__beach__z11_plus.geojson").is_file()
    assert (out_dir / "IE__woodland__z14_plus.geojson").is_file()

    beach_gdf = gpd.read_file(beach)
    assert len(beach_gdf) >= 1
    assert "name" in beach_gdf.columns

    crop_gdf = gpd.read_file(crop)
    assert len(crop_gdf) >= 1
    assert set(crop_gdf["class"].unique()) == {"crop"}
    assert set(crop_gdf["subclass"].unique()) == {"crop"}
    assert "source" in crop_gdf.columns

    wood_gdf = gpd.read_file(wood)
    assert "subclass" in wood_gdf.columns
    assert set(wood_gdf["class"].unique()) == {"woodland"}
    assert set(wood_gdf["subclass"]).issubset({"coniferous", "non_coniferous", "unknown"})

    # orchard is a dedicated layer (not crop)
    assert meta["counts"]["by_class"].get("crop", 0) == 1
    assert meta["counts"]["by_class"].get("orchard", 0) == 1
    orchard = out_dir / "IE__orchard__z11_plus.gpkg"
    assert orchard.is_file()
    assert meta["tile_layer_names"]["beach"] == "beach"
    assert meta["tile_layer_names"]["orchard"] == "orchard"

    attr = out_dir / "IE__ATTRIBUTION.txt"
    assert attr.is_file()
    assert "OpenStreetMap" in attr.read_text(encoding="utf-8")
    assert (out_dir / "IE__landcover__all.gpkg").is_file()
    assert (out_dir / "IE__mapbox-style.json").is_file()
    combined = gpd.read_file(out_dir / "IE__landcover__all.gpkg")
    assert set(combined.columns) >= {"id", "class", "subclass", "name", "source", "geometry"}
    assert "Point" not in set(combined.geom_type)
    assert (out_dir / "IE__landcover_point__all.gpkg").is_file()


def test_cli_dry_run(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "ingest",
            "--scope",
            "country",
            "--country",
            "PL",
            "--dry-run",
            "--output-dir",
            str(tmp_path / "out"),
            "--local-root",
            str(FIXTURES),
        ],
    )
    assert result.exit_code == 0, result.output
    plan = tmp_path / "out" / "PL__dry_run_plan.json"
    assert plan.is_file()
    data = json.loads(plan.read_text(encoding="utf-8"))
    assert data["scope"] == "country"
    assert data["extracts"]
    assert data["clms"]["service"] == "Copernicus Land Monitoring Service"
    assert data["clms"]["used"] is True
    assert "clc" not in data["clms"]["datasets"]["woodland"]
    assert "parallel" in data


def test_resume_skips_completed(tmp_path: Path):
    out = tmp_path / "out"
    settings = Settings(
        output_dir=out,
        cache_dir=out / "cache",
        temp_dir=out / "tmp",
        concurrency=1,
    )
    first = run_pipeline(
        scope="country",
        country="IE",
        settings=settings,
        local_root=FIXTURES,
        offline=True,
    )
    resume_file = out / "IE__resume.json"
    assert resume_file.is_file()
    state = json.loads(resume_file.read_text(encoding="utf-8"))
    assert state["completed_units"]
    assert first.stats["accepted"] >= 8

    # Second run with resume reloads unit cache; does not re-download/re-parse sources
    unit_cache = out / "tmp" / "units" / "IE"
    assert any(unit_cache.glob("*.json"))
    result = run_pipeline(
        scope="country",
        country="IE",
        resume=True,
        settings=settings,
        local_root=FIXTURES,
        offline=True,
    )
    assert result.stats["accepted"] == first.stats["accepted"]
    assert (out / "IE__beach__z11_plus.gpkg").is_file()
