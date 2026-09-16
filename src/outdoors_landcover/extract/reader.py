"""Read OSM-derived vector features from GeoJSON/GPKG/OSM XML."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.geometry.base import BaseGeometry

from outdoors_landcover.exceptions import ExtractUnavailableError
from outdoors_landcover.logging import get_logger

logger = get_logger(__name__)

OSM_TAG_KEYS = (
    "natural",
    "landuse",
    "leisure",
    "wetland",
    "boundary",
    "name",
    "leaf_type",
    "leaf_cycle",
    "species",
    "genus",
    "wood:type",
    "forest_type",
    "osm_id",
    "id",
    "@id",
)


def _tags_from_properties(props: dict[str, Any]) -> dict[str, Any]:
    tags: dict[str, Any] = {}
    # Nested tags dict (common in Overpass GeoJSON)
    nested = props.get("tags")
    if isinstance(nested, dict):
        tags.update(nested)
    for key, val in props.items():
        if key in {"tags", "geometry", "type"}:
            continue
        if val is None or val == "":
            continue
        # Strip osmium/ogr prefixes
        clean = key[5:] if key.startswith("osm_") and key not in {"osm_id"} else key
        if clean.startswith("@"):
            clean = clean[1:]
        tags[clean] = val
    # Normalise id
    if "osm_id" not in tags and "id" in tags:
        tags["osm_id"] = tags["id"]
    return tags


def iter_geojson_features(path: Path) -> Iterator[tuple[dict[str, Any], BaseGeometry]]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    features = data.get("features") if isinstance(data, dict) else data
    if not isinstance(features, list):
        raise ExtractUnavailableError(f"Invalid GeoJSON feature collection: {path}")
    for feat in features:
        if not isinstance(feat, dict):
            continue
        geom_raw = feat.get("geometry")
        if not geom_raw:
            continue
        try:
            geom = shape(geom_raw)
        except Exception:
            continue
        if geom.is_empty:
            continue
        props = feat.get("properties") or {}
        yield _tags_from_properties(props), geom


def iter_osm_xml_features(path: Path) -> Iterator[tuple[dict[str, Any], BaseGeometry]]:
    """Minimal OSM XML reader for ways with inline node refs (fixtures only)."""
    tree = ET.parse(path)
    root = tree.getroot()
    nodes: dict[str, tuple[float, float]] = {}
    for node in root.findall("node"):
        nid = node.get("id")
        lon = node.get("lon")
        lat = node.get("lat")
        if nid is None or lon is None or lat is None:
            continue
        nodes[nid] = (float(lon), float(lat))
        tags = {t.get("k"): t.get("v") for t in node.findall("tag") if t.get("k")}
        if tags:
            yield {**tags, "osm_id": nid}, Point(float(lon), float(lat))

    for way in root.findall("way"):
        wid = way.get("id") or ""
        tags = {t.get("k"): t.get("v") for t in way.findall("tag") if t.get("k")}
        if not tags:
            continue
        coords = []
        for nd in way.findall("nd"):
            ref = nd.get("ref")
            if ref and ref in nodes:
                coords.append(nodes[ref])
        if len(coords) < 2:
            continue
        if coords[0] == coords[-1] and len(coords) >= 4:
            geom: BaseGeometry = Polygon(coords)
        else:
            geom = LineString(coords)
        yield {**tags, "osm_id": wid}, geom


def iter_vector_file_features(path: Path) -> Iterator[tuple[dict[str, Any], BaseGeometry]]:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif str(gdf.crs).upper() not in {"EPSG:4326", "WGS84"}:
        gdf = gdf.to_crs("EPSG:4326")
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        props = {k: row[k] for k in gdf.columns if k != "geometry"}
        yield _tags_from_properties(props), geom


def iter_source_features(path: Path) -> Iterator[tuple[dict[str, Any], BaseGeometry]]:
    """Dispatch by file extension."""
    suffix = path.suffix.lower()
    name = path.name.lower()
    if name.endswith(".osm.pbf") or suffix == ".pbf":
        # Prefer GDAL OSM driver via pyogrio/geopandas when available.
        try:
            yield from iter_vector_file_features(path)
            return
        except Exception as exc:
            raise ExtractUnavailableError(
                f"Cannot read PBF {path}. Install GDAL with OSM support or "
                f"pre-convert to GeoJSON. Underlying error: {exc}"
            ) from exc
    if suffix in {".geojson", ".json"}:
        if path.stat().st_size > 32_000_000:
            yield from iter_vector_file_features(path)
            return
        yield from iter_geojson_features(path)
        return
    if suffix == ".osm":
        yield from iter_osm_xml_features(path)
        return
    if suffix in {".gpkg", ".shp"}:
        yield from iter_vector_file_features(path)
        return
    raise ExtractUnavailableError(f"Unsupported source format: {path}")
