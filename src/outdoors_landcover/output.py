"""Output layer writing and metadata."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from outdoors_landcover import __version__
from outdoors_landcover.logging import get_logger
from outdoors_landcover.mapping.loader import GeneralisationConfig, MappingsConfig

logger = get_logger(__name__)

# Internal columns used during ingest / dissolve (before write).
LAYER_ATTR_ORDER: dict[str, list[str]] = {
    "beach": ["id", "name"],
    "sand": ["id", "natural"],
    "marshland": ["id", "type", "area_km2"],
    "marshland_point": ["id", "type", "area_km2"],
    "park": ["id", "landuse"],
    "orchard": ["id", "landuse", "name"],
    "vineyard": ["id", "landuse", "name"],
    "crop": ["id", "landcover"],
    "grass": ["id", "landcover"],
    "woodland": ["id", "forest_type", "name"],
    "woodland_point": ["id", "forest_type"],
}

# Written to GPKG / GeoJSON / Mapbox for every layer.
OUTPUT_ATTR_ORDER: list[str] = ["id", "class", "subclass", "name", "source", "area_km2"]

_SUBCLASS_FIELD = {
    "woodland": "forest_type",
    "woodland_point": "forest_type",
    "marshland": "type",
    "marshland_point": "type",
    "crop": "landcover",
    "grass": "landcover",
    "park": "landuse",
    "orchard": "landuse",
    "vineyard": "landuse",
    "sand": "natural",
}


def unify_gdf(gdf: gpd.GeoDataFrame, logical: str) -> gpd.GeoDataFrame:
    """Map per-layer OSM/CLMS columns onto one schema for QGIS and Mapbox."""
    if gdf.empty:
        out = gpd.GeoDataFrame(
            columns=[*OUTPUT_ATTR_ORDER, "geometry"], geometry=[], crs="EPSG:4326"
        )
        return out

    out = gdf.copy()
    if out.crs is None:
        out = out.set_crs("EPSG:4326")
    out["class"] = logical
    field = _SUBCLASS_FIELD.get(logical)
    if "subclass" in out.columns and field is None:
        out["subclass"] = out["subclass"].fillna(logical).astype(str)
    elif field and field in out.columns:
        out["subclass"] = out[field].fillna(logical).astype(str)
    elif "subclass" not in out.columns:
        out["subclass"] = logical
    else:
        out["subclass"] = out["subclass"].fillna(logical).astype(str)
    if "name" not in out.columns:
        out["name"] = ""
    else:
        out["name"] = out["name"].fillna("").astype(str)
    if "source" not in out.columns:
        out["source"] = "osm"
    else:
        out["source"] = out["source"].fillna("osm").astype(str)
    if "id" not in out.columns:
        out["id"] = [str(i) for i in range(len(out))]
    else:
        out["id"] = out["id"].astype(str)
    if "area_km2" in out.columns:
        out["area_km2"] = pd.to_numeric(out["area_km2"], errors="coerce")
    else:
        out["area_km2"] = pd.NA
    keep = [*OUTPUT_ATTR_ORDER, "geometry"]
    out = out[keep]
    if "id" in out.columns:
        out = out.sort_values("id").reset_index(drop=True)
    return out


def layer_filename(logical: str, profile: str, scope_label: str, ext: str = "gpkg") -> str:
    return f"{scope_label}__{logical}__{profile}.{ext}"


def write_layer(
    gdf: gpd.GeoDataFrame,
    path: Path,
    *,
    layer_name: str,
    logical: str,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = unify_gdf(gdf, logical)
    if path.exists():
        path.unlink()
    suffix = path.suffix.lower()
    if suffix in {".geojson", ".json"}:
        out.to_file(path, driver="GeoJSON")
    else:
        out.to_file(path, layer=layer_name, driver="GPKG")
    logger.info("Wrote %s features → %s (layer=%s)", len(out), path, layer_name)
    return path


def write_combined_landcover(
    layer_files: list[Path],
    dest: Path,
    *,
    layer_name: str = "landcover",
) -> Path | None:
    """Stack all per-class outputs into one table with the unified schema."""
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    suffix = dest.suffix.lower()
    geojson_frames: list[gpd.GeoDataFrame] = []
    for path in layer_files:
        if not path.is_file() or path.stat().st_size == 0:
            continue
        try:
            gdf = gpd.read_file(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skip %s for combined output: %s", path, exc)
            continue
        if gdf.empty:
            continue
        stem_parts = path.stem.split("__")
        logical = stem_parts[1] if len(stem_parts) >= 3 else layer_name
        unified = unify_gdf(gdf, logical)
        if suffix in {".geojson", ".json"}:
            geojson_frames.append(unified)
        else:
            unified.to_file(
                dest,
                layer=layer_name,
                driver="GPKG",
                mode="w" if written == 0 else "a",
            )
        written += len(unified)
        del gdf, unified
    if written == 0:
        return None
    if suffix in {".geojson", ".json"}:
        combined = gpd.GeoDataFrame(pd.concat(geojson_frames, ignore_index=True), crs="EPSG:4326")
        combined.to_file(dest, driver="GeoJSON")
    logger.info("Wrote combined %s features → %s", written, dest)
    return dest


def mapbox_style_spec(*, tileset_url: str = "mapbox://outdoors.landcover") -> dict[str, Any]:
    """Mapbox GL style layers that filter the unified `class` attribute."""
    fill = [
        ("crop", "#e8d48a"),
        ("grass", "#c5e8a8"),
        ("woodland", "#3d8c57"),
        ("marshland", "#7bb3a0"),
        ("park", "#8fce6b"),
        ("orchard", "#b5d47a"),
        ("vineyard", "#9b6b9b"),
        ("sand", "#edd9a3"),
        ("beach", "#f5e6c8"),
    ]
    layers: list[dict[str, Any]] = []
    for i, (cls, color) in enumerate(fill):
        paint: dict[str, Any] = {"fill-color": color, "fill-opacity": 0.75}
        if cls == "woodland":
            paint["fill-color"] = [
                "match",
                ["get", "subclass"],
                "coniferous",
                "#2f6b45",
                "non_coniferous",
                "#5aa05f",
                "#3d8c57",
            ]
        layers.append(
            {
                "id": f"landcover-{cls}",
                "type": "fill",
                "source": "outdoors-landcover",
                "source-layer": cls,
                "filter": ["==", ["get", "class"], cls],
                "paint": paint,
                "layout": {"visibility": "visible"},
                "metadata": {"sort": i},
            }
        )
    layers.append(
        {
            "id": "landcover-woodland-point",
            "type": "circle",
            "source": "outdoors-landcover",
            "source-layer": "woodland_point",
            "filter": ["==", ["get", "class"], "woodland_point"],
            "paint": {"circle-radius": 3, "circle-color": "#2f6b45"},
        }
    )
    layers.append(
        {
            "id": "landcover-marshland-point",
            "type": "circle",
            "source": "outdoors-landcover",
            "source-layer": "marshland_point",
            "filter": ["==", ["get", "class"], "marshland_point"],
            "paint": {"circle-radius": 3, "circle-color": "#4f8f84"},
        }
    )
    return {
        "version": 8,
        "name": "Outdoors landcover",
        "sources": {
            "outdoors-landcover": {
                "type": "vector",
                "url": tileset_url,
                "attribution": (
                    "© OpenStreetMap contributors, "
                    "© European Union, Copernicus Land Monitoring Service"
                ),
            }
        },
        "layers": layers,
        "metadata": {
            "attributes": OUTPUT_ATTR_ORDER,
            "class_values": [cls for cls, _color in fill]
            + ["woodland_point", "marshland_point"],
        },
    }


def build_metadata(
    *,
    scope: str,
    scope_label: str,
    country: dict[str, Any] | None,
    extracts: list[dict[str, Any]],
    mappings: MappingsConfig,
    generalisation: GeneralisationConfig,
    counts: dict[str, Any],
    outputs: list[dict[str, Any]],
    clms: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "pipeline": "outdoors-landcover",
        "pipeline_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "scope_label": scope_label,
        "country": country,
        "source": {
            "type": "OpenStreetMap",
            "provider": "geofabrik",
            "licence": mappings.licence,
            "attribution": mappings.attribution,
            "extracts": extracts,
        },
        "mappings_version": mappings.version,
        "generalisation_version": generalisation.version,
        "tile_layer_names": generalisation.tile_layer_names,
        "attribute_schema": OUTPUT_ATTR_ORDER,
        "counts": counts,
        "outputs": outputs,
        "notes": [
            "area_km2 is retained on marshland layers for zoom filtering and styling.",
            "orchard and vineyard are dedicated layers (not folded into crop).",
            "plant_nursery is intentionally unmapped.",
            (
                "woodland_point uses wood/forest nodes or large-polygon centroids; "
                "natural=tree is ignored."
            ),
            (
                "All vector outputs share id, class, subclass, name, source, area_km2 "
                "so Mapbox and QGIS can style every layer the same way."
            ),
        ],
    }
    if clms:
        payload["clms"] = clms
    return payload


def write_metadata(path: Path, metadata: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return path


def write_attribution(
    path: Path,
    attribution: str,
    licence: str,
    extra_lines: list[str] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"{attribution}\nData licence: {licence}\n"
        "https://www.openstreetmap.org/copyright\n"
    )
    if extra_lines:
        body += "\n" + "\n".join(extra_lines) + "\n"
        body += "https://land.copernicus.eu/\n"
    path.write_text(body, encoding="utf-8")
    return path
