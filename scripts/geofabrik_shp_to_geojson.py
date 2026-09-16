#!/usr/bin/env python3
"""Convert Geofabrik free shapefiles to OSM-tagged GeoJSON for outdoors-landcover."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import mapping

# Geofabrik fclass → OSM tags consumed by config/mappings.yaml
LANDUSE_FCLASS: dict[str, dict[str, str]] = {
    "forest": {"landuse": "forest"},
    "farmland": {"landuse": "farmland"},
    "farm": {"landuse": "farm"},
    "meadow": {"landuse": "meadow"},
    "grass": {"landuse": "grass"},
    "orchard": {"landuse": "orchard"},
    "vineyard": {"landuse": "vineyard"},
    "recreation_ground": {"landuse": "recreation_ground"},
    "park": {"leisure": "park"},
    "nature_reserve": {"leisure": "nature_reserve"},
    "national_park": {"boundary": "national_park"},
    "village_green": {"landuse": "village_green"},
}

NATURAL_FCLASS: dict[str, dict[str, str]] = {
    "beach": {"natural": "beach"},
    "sand": {"natural": "sand"},
    "wetland": {"natural": "wetland"},
    "grassland": {"natural": "grassland"},
    "heath": {"natural": "grassland"},
}


def _features_from_zip(zip_path: Path) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    with zipfile.ZipFile(zip_path) as zf:
        members = [
            n
            for n in zf.namelist()
            if n.endswith(".shp") and ("landuse_a" in n or "natural_a" in n)
        ]

    for member in members:
        uri = f"/vsizip/{zip_path}/{member}"
        try:
            gdf = gpd.read_file(uri)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {member}: {exc}")
            continue
        if gdf.empty or "fclass" not in gdf.columns:
            continue
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        elif str(gdf.crs).upper() not in {"EPSG:4326", "WGS84"}:
            gdf = gdf.to_crs("EPSG:4326")

        is_natural = "natural_a" in member
        tag_map = NATURAL_FCLASS if is_natural else LANDUSE_FCLASS
        wanted = set(tag_map)
        gdf = gdf[gdf["fclass"].astype(str).str.lower().isin(wanted)].copy()
        if gdf.empty:
            print(f"  {zip_path.name} :: {Path(member).name}: kept 0")
            continue

        kept = 0
        for idx, row in gdf.iterrows():
            fclass = str(row["fclass"]).strip().lower()
            tags = tag_map[fclass]
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            osm_id = row.get("osm_id")
            if osm_id is None or (isinstance(osm_id, float) and osm_id != osm_id):
                osm_id = f"{zip_path.stem}_{member}_{idx}"
            props: dict[str, Any] = {"osm_id": str(int(osm_id) if str(osm_id).isdigit() else osm_id)}
            props.update(tags)
            name = row.get("name")
            if name is not None and str(name).strip() and str(name).lower() != "nan":
                props["name"] = str(name)
            features.append(
                {
                    "type": "Feature",
                    "properties": props,
                    "geometry": mapping(geom),
                }
            )
            kept += 1
        print(f"  {zip_path.name} :: {Path(member).name}: kept {kept}")
    return features


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shp-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    zips = sorted(args.shp_dir.glob("*-latest-free.shp.zip"))
    if not zips:
        raise SystemExit(f"No *-latest-free.shp.zip files in {args.shp_dir}")

    all_features: list[dict[str, Any]] = []
    for zp in zips:
        print(f"==> Reading {zp.name}")
        all_features.extend(_features_from_zip(zp))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": all_features}, fh)
    print(f"==> Wrote {len(all_features)} features → {args.output}")


if __name__ == "__main__":
    main()
