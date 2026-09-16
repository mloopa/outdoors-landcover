"""Tests for GeoJSON / tippecanoe export helpers."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point, box
from typer.testing import CliRunner

from outdoors_landcover.cli import app
from outdoors_landcover.export_tiles import (
    export_dir_to_geojson,
    parse_layer_stem,
    write_tippecanoe_manifest,
)


def test_parse_layer_stem():
    assert parse_layer_stem("PL__woodland__z14_plus") == ("PL", "woodland", "z14_plus")
    assert parse_layer_stem("bad") is None


def test_export_dir_to_geojson(tmp_path: Path):
    gpkg = tmp_path / "PL__beach__z11_plus.gpkg"
    gdf = gpd.GeoDataFrame(
        [{"id": "a", "name": "X", "geometry": box(0, 0, 1, 1)}],
        crs="EPSG:4326",
    )
    gdf.to_file(gpkg, layer="beach", driver="GPKG")
    # point layer too
    pt = tmp_path / "PL__woodland_point__z11_plus.gpkg"
    gpd.GeoDataFrame(
        [{"id": "p", "forest_type": "unknown", "geometry": Point(0, 0)}],
        crs="EPSG:4326",
    ).to_file(pt, layer="woodland_point", driver="GPKG")

    out = export_dir_to_geojson(tmp_path, tmp_path / "geojson")
    assert len(out) == 2
    assert all(p.suffix == ".geojson" for p in out)
    loaded = gpd.read_file(tmp_path / "geojson" / "PL__beach__z11_plus.geojson")
    assert len(loaded) == 1
    assert loaded.iloc[0]["name"] == "X"
    assert loaded.iloc[0]["class"] == "beach"
    assert loaded.iloc[0]["subclass"] == "beach"
    assert loaded.iloc[0]["source"] == "osm"

    manifest = write_tippecanoe_manifest(out, tmp_path / "geojson" / "build_mbtiles.sh")
    text = manifest.read_text(encoding="utf-8")
    assert "tippecanoe" in text
    assert "beach" in text


def test_cli_export(tmp_path: Path):
    gpkg = tmp_path / "PL__park__z11_plus.gpkg"
    gpd.GeoDataFrame(
        [{"id": "1", "landuse": "park", "geometry": box(0, 0, 0.1, 0.1)}],
        crs="EPSG:4326",
    ).to_file(gpkg, layer="park", driver="GPKG")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "export",
            "--input-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "geojson"),
            "--mbtiles",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "geojson" / "PL__park__z11_plus.geojson").is_file()
    assert (tmp_path / "geojson" / "build_mbtiles.sh").is_file()
