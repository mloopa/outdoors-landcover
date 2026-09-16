"""Geometry validation, repair, area, and stable IDs."""

from __future__ import annotations

import hashlib
from typing import Any

from pyproj import Geod
from shapely import make_valid
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from outdoors_landcover.logging import get_logger

logger = get_logger(__name__)
_GEOD = Geod(ellps="WGS84")


def stable_feature_id(osm_id: Any, feature_class: str, geom: BaseGeometry) -> str:
    raw = f"{feature_class}:{osm_id}:{geom.wkt}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def repair_geometry(geom: BaseGeometry) -> BaseGeometry | None:
    if geom is None or geom.is_empty:
        return None
    if geom.is_valid:
        return geom
    try:
        fixed = make_valid(geom)
    except Exception:
        return None
    if fixed is None or fixed.is_empty:
        return None
    # Prefer polygonal result for area features
    if fixed.geom_type in {"GeometryCollection", "MultiLineString", "LineString"}:
        candidates = getattr(fixed, "geoms", [fixed])
        polys = [g for g in candidates if g.geom_type in {"Polygon", "MultiPolygon"}]
        if not polys:
            return None
        from shapely.ops import unary_union

        fixed = unary_union(polys)
    return fixed if not fixed.is_empty else None


def area_km2_geodesic(geom: BaseGeometry) -> float:
    """Geodesic area in km² for WGS84 geometries."""
    if geom is None or geom.is_empty:
        return 0.0
    geoms = list(geom.geoms) if geom.geom_type.startswith("Multi") else [geom]
    total = 0.0
    for g in geoms:
        if g.geom_type != "Polygon":
            continue
        lon, lat = g.exterior.coords.xy
        area_m2, _ = _GEOD.polygon_area_perimeter(lon, lat)
        total += abs(area_m2)
        for interior in g.interiors:
            ilon, ilat = interior.coords.xy
            iarea, _ = _GEOD.polygon_area_perimeter(ilon, ilat)
            total -= abs(iarea)
    return total / 1_000_000.0


def area_m2_projected(geom: BaseGeometry, to_metric) -> float:
    """Area in m² after projecting with a pyproj Transformer.transform callable."""
    projected = transform(to_metric, geom)
    return float(projected.area)


def is_supported_polygon(geom: BaseGeometry) -> bool:
    return geom.geom_type in {"Polygon", "MultiPolygon"}


def is_supported_point(geom: BaseGeometry) -> bool:
    return geom.geom_type in {"Point", "MultiPoint"}
