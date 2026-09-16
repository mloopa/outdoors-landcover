"""Choose OSM vs CLMS per layer/profile by native resolution."""

from __future__ import annotations

from typing import Any

from outdoors_landcover.mapping.loader import GeneralisationProfile


def clms_has_better_resolution(
    profile: GeneralisationProfile,
    *,
    native_resolution_m: float,
) -> bool:
    """True when CLMS pixels are finer than this OSM generalisation profile."""
    return native_resolution_m < float(profile.simplify_tolerance_m)


def select_source_rows(
    *,
    logical: str,
    profile: GeneralisationProfile,
    osm_rows: list[dict[str, Any]],
    clms_rows: list[dict[str, Any]] | None,
    native_resolution_m: float,
) -> tuple[list[dict[str, Any]], str]:
    """
    Return (rows, source_name).

    CLMS High Resolution Layers (10 m) win when they are finer than the OSM
    simplify tolerance for this zoom profile. Otherwise OSM is kept (including
    detailed woodland at z14+). Empty CLMS falls back to OSM.
    """
    if clms_rows and clms_has_better_resolution(
        profile, native_resolution_m=native_resolution_m
    ):
        return clms_rows, "clms"
    return osm_rows, "osm"
