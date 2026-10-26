"""YAML config loaders for mappings and generalisation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from outdoors_landcover.exceptions import ConfigurationError


@dataclass(frozen=True)
class MatchRule:
    match: dict[str, str]
    set_attrs: dict[str, str] = field(default_factory=dict)
    retain: tuple[str, ...] = ()
    derive_forest_type: bool = False


@dataclass
class FeatureMapping:
    name: str
    geometry_types: tuple[str, ...]
    output_attributes: tuple[str, ...]
    rules: list[MatchRule]
    zoom_min: int | None = None
    zoom_profiles: tuple[str, ...] = ()
    point_layer: str | None = None
    point_zoom_min: int | None = None
    min_area_km2_for_point: float | None = None
    accept_point_tags: bool = False
    priority: int = 100


@dataclass
class ForestTypeConfig:
    coniferous_any_of: list[dict[str, str]]
    coniferous_species: list[str]
    non_coniferous_any_of: list[dict[str, str]]
    non_coniferous_species: list[str]
    fallback: str = "unknown"


@dataclass
class MappingsConfig:
    version: int
    attribution: str
    licence: str
    features: dict[str, FeatureMapping]
    forest_type: ForestTypeConfig
    exclusions: list[dict[str, str]]
    priority: dict[str, int]


@dataclass
class GeneralisationProfile:
    name: str
    description: str
    min_area_m2: float
    simplify_tolerance_m: float
    dissolve_same_attributes: bool
    repair: bool
    # Attribute columns to dissolve on; [] merges everything into one group.
    # None falls back to dissolve_same_attributes behaviour.
    dissolve_by: list[str] | None = None
    close_gap_m: float = 0.0
    open_width_m: float = 0.0
    fill_holes_below_m2: float = 0.0
    tile_size_m: float = 100_000.0

    @property
    def uses_area_generalisation(self) -> bool:
        return (
            self.dissolve_by is not None
            or self.close_gap_m > 0
            or self.open_width_m > 0
            or self.fill_holes_below_m2 > 0
        )


@dataclass
class GeneralisationConfig:
    version: int
    crs_for_area: str
    output_crs: str
    profiles: dict[str, GeneralisationProfile]
    layer_profiles: dict[str, list[str]]
    tile_layer_names: dict[str, str]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigurationError(f"Config must be a mapping: {path}")
    return data


def load_mappings(path: Path) -> MappingsConfig:
    data = _load_yaml(path)
    priority = {str(k): int(v) for k, v in (data.get("priority") or {}).items()}
    features: dict[str, FeatureMapping] = {}
    for name, raw in (data.get("features") or {}).items():
        rules = []
        for rule in raw.get("rules") or []:
            rules.append(
                MatchRule(
                    match={str(k): str(v) for k, v in (rule.get("match") or {}).items()},
                    set_attrs={str(k): str(v) for k, v in (rule.get("set") or {}).items()},
                    retain=tuple(rule.get("retain") or []),
                    derive_forest_type=bool(rule.get("derive_forest_type", False)),
                )
            )
        features[name] = FeatureMapping(
            name=name,
            geometry_types=tuple(raw.get("geometry_types") or ["Polygon", "MultiPolygon"]),
            output_attributes=tuple(raw.get("output_attributes") or []),
            rules=rules,
            zoom_min=raw.get("zoom_min"),
            zoom_profiles=tuple(raw.get("zoom_profiles") or []),
            point_layer=raw.get("point_layer"),
            point_zoom_min=raw.get("point_zoom_min"),
            min_area_km2_for_point=raw.get("min_area_km2_for_point"),
            accept_point_tags=bool(raw.get("accept_point_tags", False)),
            priority=priority.get(name, 100),
        )

    ft = data.get("forest_type") or {}
    forest = ForestTypeConfig(
        coniferous_any_of=list((ft.get("coniferous") or {}).get("any_of") or []),
        coniferous_species=list((ft.get("coniferous") or {}).get("species_contains") or []),
        non_coniferous_any_of=list((ft.get("non_coniferous") or {}).get("any_of") or []),
        non_coniferous_species=list((ft.get("non_coniferous") or {}).get("species_contains") or []),
        fallback=str(ft.get("fallback") or "unknown"),
    )
    exclusions = list((data.get("exclusions") or {}).get("never_crop_or_grass_if") or [])
    return MappingsConfig(
        version=int(data.get("version") or 1),
        attribution=str(data.get("attribution") or "© OpenStreetMap contributors"),
        licence=str(data.get("licence") or "ODbL 1.0"),
        features=features,
        forest_type=forest,
        exclusions=exclusions,
        priority=priority,
    )


def load_generalisation(path: Path) -> GeneralisationConfig:
    data = _load_yaml(path)
    profiles: dict[str, GeneralisationProfile] = {}
    for name, raw in (data.get("profiles") or {}).items():
        profiles[name] = GeneralisationProfile(
            name=name,
            description=str(raw.get("description") or ""),
            min_area_m2=float(raw.get("min_area_m2") or 0),
            simplify_tolerance_m=float(raw.get("simplify_tolerance_m") or 0),
            dissolve_same_attributes=bool(raw.get("dissolve_same_attributes", False)),
            repair=bool(raw.get("repair", True)),
            dissolve_by=(
                None if raw.get("dissolve_by") is None else [str(c) for c in raw["dissolve_by"]]
            ),
            close_gap_m=float(raw.get("close_gap_m") or 0),
            open_width_m=float(raw.get("open_width_m") or 0),
            fill_holes_below_m2=float(raw.get("fill_holes_below_m2") or 0),
            tile_size_m=float(raw.get("tile_size_m") or 100_000),
        )
    return GeneralisationConfig(
        version=int(data.get("version") or 1),
        crs_for_area=str(data.get("crs_for_area") or "EPSG:3857"),
        output_crs=str(data.get("output_crs") or "EPSG:4326"),
        profiles=profiles,
        layer_profiles={k: list(v) for k, v in (data.get("layer_profiles") or {}).items()},
        tile_layer_names={str(k): str(v) for k, v in (data.get("tile_layer_names") or {}).items()},
    )
