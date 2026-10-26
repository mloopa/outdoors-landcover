"""Zoom-dependent generalisation."""

from __future__ import annotations

import hashlib
import math
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import geopandas as gpd
import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from outdoors_landcover.logging import get_logger
from outdoors_landcover.mapping.loader import GeneralisationConfig, GeneralisationProfile
from outdoors_landcover.transform.geometry import area_km2_geodesic, repair_geometry

logger = get_logger(__name__)

# Fewer segments per quarter circle keeps buffers fast; output is simplified anyway.
_QUAD_SEGS = 4
# Snapping grid (metric units) so pieces from neighbouring tiles share exact edges.
_SEAM_PRECISION_M = 0.01


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
    """Repair, filter by area, simplify; dissolve/smooth when the profile asks for it."""
    if gdf.empty:
        return gdf
    if profile.uses_area_generalisation or profile.dissolve_same_attributes:
        group_cols = profile.dissolve_by
        if group_cols is None:
            group_cols = list(attribute_cols or [])
        return generalise_area_gdf(gdf, profile, config, group_cols=group_cols)
    return _generalise_per_feature(gdf, profile, config)


def _generalise_per_feature(
    gdf: gpd.GeoDataFrame,
    profile: GeneralisationProfile,
    config: GeneralisationConfig,
) -> gpd.GeoDataFrame:
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
    return gpd.GeoDataFrame(records, crs=config.output_crs).reset_index(drop=True)


def generalise_area_gdf(
    gdf: gpd.GeoDataFrame,
    profile: GeneralisationProfile,
    config: GeneralisationConfig,
    *,
    group_cols: list[str],
    max_workers: int | None = None,
) -> gpd.GeoDataFrame:
    """
    Low-zoom area generalisation.

    Per dissolve group: union → close gaps (buffer out/in) → remove thin parts
    (buffer in/out) → fill small holes → simplify → drop parts below min_area.
    The union and morphology run on a tile grid so country/continent layers
    fit in memory; tiles overlap by the morphology radius so seams match.
    """
    src = gdf if gdf.crs is not None else gdf.set_crs("EPSG:4326")
    metric = src.to_crs(config.crs_for_area)
    geoms = _polygonal(np.asarray(metric.geometry.values, dtype=object), repair=profile.repair)

    present = [c for c in group_cols if c in metric.columns]
    carry = [c for c in ("source",) if c in metric.columns and c not in present]
    if present:
        keys_series = metric[present].astype(object).where(metric[present].notna(), None)
        key_tuples = [tuple(r) for r in keys_series.itertuples(index=False, name=None)]
    else:
        key_tuples = [()] * len(metric)

    groups: dict[tuple, list[int]] = {}
    for i, key in enumerate(key_tuples):
        if geoms[i] is not None:
            groups.setdefault(key, []).append(i)

    want_area = "area_km2" in metric.columns
    records: list[dict[str, Any]] = []
    for key, idx in sorted(groups.items(), key=lambda kv: str(kv[0])):
        parts = _generalise_group(geoms[idx], profile, max_workers=max_workers)
        if not parts:
            continue
        base = dict(zip(present, key))
        for c in carry:
            base[c] = metric[c].iloc[idx[0]]
        for part in parts:
            row = dict(base)
            row["id"] = _area_feature_id(profile.name, key, part)
            row["geometry"] = part
            records.append(row)

    logger.info(
        "generalise[%s]: %s input → %s output polygons (groups=%s)",
        profile.name,
        len(gdf),
        len(records),
        len(groups),
    )
    if not records:
        cols = [*present, *carry, "id"]
        return gpd.GeoDataFrame(columns=cols, geometry=[], crs=config.output_crs)

    out = gpd.GeoDataFrame(records, geometry="geometry", crs=config.crs_for_area)
    out = out.to_crs(config.output_crs)
    if want_area:
        out["area_km2"] = [round(area_km2_geodesic(g), 4) for g in out.geometry]
    return out.sort_values("id").reset_index(drop=True)


def _polygonal(geoms: np.ndarray, *, repair: bool) -> np.ndarray:
    out = np.asarray(geoms, dtype=object).copy()
    if repair:
        invalid = np.flatnonzero(~shapely.is_valid(out) & ~shapely.is_missing(out))
        for i in invalid:
            out[i] = repair_geometry(out[i])
    type_ids = shapely.get_type_id(out)
    out[~np.isin(type_ids, (3, 6))] = None  # 3 Polygon, 6 MultiPolygon
    out[shapely.is_empty(out)] = None
    return out


def _generalise_group(
    geoms: np.ndarray,
    profile: GeneralisationProfile,
    *,
    max_workers: int | None,
) -> list[Polygon]:
    geoms = np.asarray([g for g in geoms if g is not None], dtype=object)
    if len(geoms) == 0:
        return []

    close = max(profile.close_gap_m, 0.0)
    open_ = max(profile.open_width_m, 0.0)
    simplify = max(profile.simplify_tolerance_m, 0.0)

    # Dense OSM vertices are irrelevant at low zoom and make buffering slow.
    positive = [x for x in (simplify, close, open_) if x > 0]
    pre_tol = min(positive) / 4 if positive else 0.0
    if pre_tol > 0:
        geoms = _polygonal(shapely.simplify(geoms, pre_tol, preserve_topology=True), repair=True)
        geoms = geoms[~shapely.is_missing(geoms)]
        if len(geoms) == 0:
            return []

    margin = 2 * (close + open_) + simplify + 1.0
    cells = _grid_cells(shapely.total_bounds(geoms), profile.tile_size_m)
    tree = shapely.STRtree(geoms)

    def run(cell: tuple[float, float, float, float]) -> BaseGeometry | None:
        return _process_cell(tree, geoms, cell, margin=margin, close=close, open_=open_)

    workers = max_workers or min(len(cells), os.cpu_count() or 4)
    if workers > 1 and len(cells) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run, cells))
    else:
        results = list(map(run, cells))

    polys = _stitch_cells(cells, results)
    polys = [_fill_small_holes(p, profile.fill_holes_below_m2) for p in polys]

    out: list[Polygon] = []
    for p in polys:
        if simplify > 0:
            p = p.simplify(simplify, preserve_topology=True)
        if p.is_empty:
            continue
        if not p.is_valid:
            p = shapely.make_valid(p)
        for part in shapely.get_parts(p):
            if part.geom_type != "Polygon":
                continue
            part = _fill_small_holes(part, profile.fill_holes_below_m2)
            if part.area >= profile.min_area_m2:
                out.append(part)
    return out


def _stitch_cells(
    cells: list[tuple[float, float, float, float]],
    results: list[BaseGeometry | None],
) -> list[Polygon]:
    """Merge polygons split by tile edges; interior polygons pass through untouched."""
    done: list[Polygon] = []
    seam: list[Polygon] = []
    for cell, geom in zip(cells, results):
        if geom is None:
            continue
        edge = shapely.box(*cell).exterior
        for part in shapely.get_parts(geom):
            if part.geom_type != "Polygon" or part.is_empty:
                continue
            if shapely.distance(part, edge) <= _SEAM_PRECISION_M * 10:
                seam.append(part)
            else:
                done.append(part)
    if not seam:
        return done

    seam_arr = np.asarray(seam, dtype=object)
    tree = shapely.STRtree(seam_arr)
    left, right = tree.query(
        shapely.buffer(seam_arr, _SEAM_PRECISION_M * 10), predicate="intersects"
    )
    parent = list(range(len(seam_arr)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in zip(left, right):
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[ra] = rb
    components: dict[int, list[int]] = {}
    for i in range(len(seam_arr)):
        components.setdefault(find(i), []).append(i)
    for members in components.values():
        if len(members) == 1:
            done.append(seam_arr[members[0]])
            continue
        merged = shapely.union_all(seam_arr[members], grid_size=_SEAM_PRECISION_M)
        done.extend(p for p in shapely.get_parts(merged) if p.geom_type == "Polygon")
    return done


def _process_cell(
    tree: shapely.STRtree,
    geoms: np.ndarray,
    cell: tuple[float, float, float, float],
    *,
    margin: float,
    close: float,
    open_: float,
) -> BaseGeometry | None:
    minx, miny, maxx, maxy = cell
    ext = shapely.box(minx - margin, miny - margin, maxx + margin, maxy + margin)
    idx = tree.query(ext, predicate="intersects")
    if len(idx) == 0:
        return None
    clipped = shapely.intersection(geoms[idx], ext)
    clipped = clipped[~shapely.is_empty(clipped)]
    if len(clipped) == 0:
        return None
    merged = _morphology(clipped, close=close, open_=open_)
    if merged.is_empty:
        return None
    core = shapely.intersection(merged, shapely.box(*cell), grid_size=_SEAM_PRECISION_M)
    return None if core.is_empty else core


def _morphology(geoms: np.ndarray, *, close: float, open_: float) -> BaseGeometry:
    """Closing bridges gaps narrower than 2*close; opening drops parts thinner than 2*open."""
    merged = _dilate(geoms, close) if close > 0 else _union(geoms)
    erode = close + open_
    if erode <= 0 or merged.is_empty:
        return merged
    # Dilated outlines are already rounded at this scale; dropping near-collinear
    # vertices first avoids GEOS' pathological slow path on large negative buffers.
    smooth = min(x for x in (close, open_) if x > 0) / 8
    parts = _erode(merged, erode, smooth)
    if len(parts) == 0:
        return Polygon()
    if open_ > 0:
        return _dilate(parts, open_)
    return _valid_polygonal(shapely.multipolygons(parts))


def _dilate(geoms: np.ndarray, distance: float) -> BaseGeometry:
    # Dilation distributes over union; buffering pieces before the union is
    # exact and orders of magnitude faster than buffering one huge geometry.
    return _union(shapely.buffer(geoms, distance, quad_segs=_QUAD_SEGS))


def _union(geoms: np.ndarray) -> BaseGeometry:
    try:
        merged = shapely.union_all(geoms, grid_size=_SEAM_PRECISION_M)
    except shapely.errors.GEOSException:
        merged = shapely.union_all([_valid_polygonal(g) for g in geoms])
    return _valid_polygonal(merged)


def _erode(geom: BaseGeometry, distance: float, smooth: float) -> np.ndarray:
    parts = shapely.get_parts(geom)
    if smooth > 0:
        parts = shapely.simplify(parts, smooth, preserve_topology=True)
    try:
        eroded = shapely.buffer(parts, -distance, quad_segs=_QUAD_SEGS)
    except shapely.errors.GEOSException:
        eroded = np.asarray([_buffer_part(p, -distance) for p in parts], dtype=object)
    out = [
        q
        for g in eroded
        if g is not None and not g.is_empty
        for q in shapely.get_parts(_valid_polygonal(g))
        if q.geom_type == "Polygon" and not q.is_empty
    ]
    return np.asarray(out, dtype=object)


def _buffer_part(part: BaseGeometry, distance: float) -> BaseGeometry:
    for candidate in (part, part.simplify(_SEAM_PRECISION_M * 100, preserve_topology=True)):
        try:
            return _valid_polygonal(shapely.buffer(candidate, distance, quad_segs=_QUAD_SEGS))
        except shapely.errors.GEOSException:
            continue
    if distance >= 0:
        return part
    # Erosion as the complement of dilating the complement.
    frame = shapely.box(*part.bounds).buffer(-distance * 2, join_style="mitre")
    outside = shapely.buffer(frame.difference(part), -distance, quad_segs=_QUAD_SEGS)
    return _valid_polygonal(part.difference(outside))


def _valid_polygonal(geom: BaseGeometry) -> BaseGeometry:
    if geom.is_empty or geom.is_valid:
        return geom
    fixed = repair_geometry(geom)
    return fixed if fixed is not None else Polygon()


def _fill_small_holes(poly: Polygon, max_hole_m2: float) -> Polygon:
    if max_hole_m2 <= 0 or not poly.interiors:
        return poly
    keep = [ring for ring in poly.interiors if Polygon(ring).area >= max_hole_m2]
    if len(keep) == len(poly.interiors):
        return poly
    return Polygon(poly.exterior, keep)


def _grid_cells(
    bounds: np.ndarray, tile_size: float
) -> list[tuple[float, float, float, float]]:
    minx, miny, maxx, maxy = (float(v) for v in bounds)
    size = tile_size if tile_size > 0 else max(maxx - minx, maxy - miny, 1.0)
    nx = max(1, math.ceil((maxx - minx) / size))
    ny = max(1, math.ceil((maxy - miny) / size))
    return [
        (minx + i * size, miny + j * size, minx + (i + 1) * size, miny + (j + 1) * size)
        for i in range(nx)
        for j in range(ny)
    ]


def _area_feature_id(profile_name: str, key: tuple, geom: BaseGeometry) -> str:
    raw = f"{profile_name}:{key}:".encode() + shapely.to_wkb(geom)
    return hashlib.sha1(raw).hexdigest()[:16]
