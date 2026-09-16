"""Polygonize classified CLMS rasters into Outdoors landcover features."""

from __future__ import annotations

from typing import Any

import numpy as np
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from outdoors_landcover.logging import get_logger
from outdoors_landcover.mapping.clms import ClmsLayerConfig, attributes_for_value
from outdoors_landcover.transform.geometry import repair_geometry, stable_feature_id

logger = get_logger(__name__)


def polygonize_classified_raster(
    path,
    layer: ClmsLayerConfig,
    *,
    bbox_wgs84: tuple[float, float, float, float],
    polygonize_resolution_m: float,
    native_resolution_m: float,
) -> list[dict[str, Any]]:
    """Windowed read → optional resample → polygonize connected class regions."""
    import rasterio
    from rasterio.features import shapes
    from rasterio.transform import from_bounds as affine_from_bounds
    from rasterio.warp import Resampling as WarpResampling
    from rasterio.warp import reproject, transform_bounds
    from rasterio.windows import from_bounds

    rows: list[dict[str, Any]] = []
    with rasterio.open(path) as src:
        dst_crs = src.crs
        if dst_crs is None:
            logger.warning("CLMS raster has no CRS, skipping: %s", path)
            return []

        left, bottom, right, top = transform_bounds(
            "EPSG:4326", dst_crs, *bbox_wgs84, densify_pts=21
        )
        window = from_bounds(left, bottom, right, top, transform=src.transform)
        window = window.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
        if window.width <= 0 or window.height <= 0:
            return []

        data = src.read(1, window=window, boundless=False)
        transform = src.window_transform(window)
        win_left, win_bottom, win_right, win_top = rasterio.windows.bounds(window, src.transform)
        src_nodata = src.nodatavals[0] if src.nodatavals else None

        target_res = max(float(polygonize_resolution_m), float(native_resolution_m))
        pixel_m = _approx_pixel_size_m(src, transform)
        if pixel_m > 0 and target_res > pixel_m * 1.5:
            scale = pixel_m / target_res
            new_h = max(1, int(round(data.shape[0] * scale)))
            new_w = max(1, int(round(data.shape[1] * scale)))
            if new_h < data.shape[0] or new_w < data.shape[1]:
                dst = np.empty((new_h, new_w), dtype=data.dtype)
                dst_transform = affine_from_bounds(
                    win_left, win_bottom, win_right, win_top, new_w, new_h
                )
                reproject(
                    source=data,
                    destination=dst,
                    src_transform=transform,
                    src_crs=dst_crs,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    resampling=WarpResampling.mode,
                )
                data = dst
                transform = dst_transform

        if src_nodata is not None:
            nodata_mask = data == src_nodata
        else:
            nodata_mask = np.zeros(data.shape, dtype=bool)

        unique_vals = np.unique(data[~nodata_mask]) if nodata_mask.any() else np.unique(data)
        mapped: dict[int, dict[str, str]] = {}
        for raw in unique_vals:
            attrs = attributes_for_value(layer, int(raw))
            if attrs is not None:
                mapped[int(raw)] = attrs
        if not mapped:
            return []

        class_band = np.zeros(data.shape, dtype=np.int32)
        for raw, _attrs in mapped.items():
            class_band[data == raw] = raw
        mask = np.isin(class_band, list(mapped))

        for geom_mapping, value in shapes(
            class_band, mask=mask, transform=transform, connectivity=8
        ):
            attrs = mapped.get(int(value))
            if attrs is None:
                continue
            geom: BaseGeometry = shape(geom_mapping)
            if geom.is_empty:
                continue
            if str(dst_crs).upper() not in {"EPSG:4326", "WGS84"}:
                import geopandas as gpd

                gseries = gpd.GeoSeries([geom], crs=dst_crs).to_crs("EPSG:4326")
                geom = gseries.iloc[0]
            repaired = repair_geometry(geom)
            if repaired is None or repaired.is_empty:
                continue
            fid = stable_feature_id(
                f"clms:{layer.name}:{int(value)}", layer.outdoors_class, repaired
            )
            row: dict[str, Any] = dict(attrs)
            row["id"] = fid
            row["osm_id"] = fid
            row["source"] = "clms"
            row["geometry"] = repaired
            rows.append(row)

    logger.info("Polygonized %s features from %s (%s)", len(rows), path, layer.name)
    return rows


def _approx_pixel_size_m(src, transform) -> float:
    """Approximate pixel size in metres from an affine transform."""
    px = abs(transform.a)
    py = abs(transform.e)
    crs = src.crs
    if crs is not None and getattr(crs, "is_geographic", False):
        return float(((px + py) / 2.0) * 111_320.0)
    return float((px + py) / 2.0)
