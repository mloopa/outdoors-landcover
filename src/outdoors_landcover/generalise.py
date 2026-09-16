"""Zoom-dependent generalisation."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
from pyproj import Transformer
from shapely.ops import transform, unary_union

from outdoors_landcover.logging import get_logger
from outdoors_landcover.mapping.loader import GeneralisationConfig, GeneralisationProfile
from outdoors_landcover.transform.geometry import repair_geometry

logger = get_logger(__name__)


def _metric_transformer(crs_for_area: str) -> tuple[Any, Any]:
    to_metric = Transformer.from_crs("EPSG:4326", crs_for_area, always_xy=True).transform
    to_wgs = Transformer.from_crs(crs_for_area, "EPSG:4326", always_xy=True).transform
    return to_metric, to_wgs


def generalise_gdf(
    gdf: gpd.GeoDataFrame,
    profile: GeneralisationProfile,
    config: GeneralisationConfig,
    *,
    attribute_cols: list[str] | None = None,
) -> gpd.GeoDataFrame:
    """Repair, filter by area, simplify, optionally dissolve same-attribute polygons."""
    if gdf.empty:
        return gdf

    to_metric, to_wgs = _metric_transformer(config.crs_for_area)
    records = []
    rejected_small = 0
    rejected_invalid = 0

    for _, row in gdf.iterrows():
        geom = row.geometry
        if profile.repair:
            geom = repair_geometry(geom)
        if geom is None or geom.is_empty:
            rejected_invalid += 1
            continue
        metric = transform(to_metric, geom)
        if metric.area < profile.min_area_m2:
            rejected_small += 1
            continue
        if profile.simplify_tolerance_m > 0:
            metric = metric.simplify(profile.simplify_tolerance_m, preserve_topology=True)
            if metric.is_empty:
                rejected_invalid += 1
                continue
            if not metric.is_valid:
                repaired = repair_geometry(transform(to_wgs, metric))
                if repaired is None:
                    rejected_invalid += 1
                    continue
                metric = transform(to_metric, repaired)
        out_geom = transform(to_wgs, metric)
        props = {c: row[c] for c in gdf.columns if c != "geometry"}
        props["geometry"] = out_geom
        records.append(props)

    logger.debug(
        "generalise[%s]: kept=%s rejected_small=%s rejected_invalid=%s",
        profile.name,
        len(records),
        rejected_small,
        rejected_invalid,
    )
    if not records:
        return gpd.GeoDataFrame(columns=gdf.columns, geometry=[], crs=config.output_crs)

    out = gpd.GeoDataFrame(records, crs=config.output_crs)
    if profile.dissolve_same_attributes and attribute_cols:
        present = [c for c in attribute_cols if c in out.columns]
        if present:
            dissolved = []
            for keys, group in out.groupby(present, dropna=False):
                if not isinstance(keys, tuple):
                    keys = (keys,)
                merged = unary_union(list(group.geometry))
                if merged.is_empty:
                    continue
                row_data = dict(zip(present, keys))
                row_data["geometry"] = merged
                # Preserve first id if present
                if "id" in group.columns:
                    row_data["id"] = sorted(group["id"].astype(str))[0]
                dissolved.append(row_data)
            out = gpd.GeoDataFrame(dissolved, crs=config.output_crs)
    return out.reset_index(drop=True)
