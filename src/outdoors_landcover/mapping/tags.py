"""Tag matching and feature classification with priority rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from outdoors_landcover.mapping.forest import derive_forest_type
from outdoors_landcover.mapping.loader import FeatureMapping, MappingsConfig


@dataclass(frozen=True)
class ClassifiedFeature:
    feature_class: str
    attributes: dict[str, Any]
    priority: int


def _norm_tags(tags: dict[str, Any]) -> dict[str, str]:
    return {str(k).lower(): str(v).lower() if v is not None else "" for k, v in tags.items()}


def _rule_matches(tags: dict[str, str], match: dict[str, str]) -> bool:
    return all(tags.get(k.lower()) == v.lower() for k, v in match.items())


def _excluded_from_vegetation(tags: dict[str, str], config: MappingsConfig) -> bool:
    return any(_rule_matches(tags, excl) for excl in config.exclusions)


def _apply_rule(
    feature: FeatureMapping,
    rule_match: dict[str, str],
    set_attrs: dict[str, str],
    retain: tuple[str, ...],
    derive_ft: bool,
    raw_tags: dict[str, Any],
    config: MappingsConfig,
) -> ClassifiedFeature | None:
    tags = _norm_tags(raw_tags)
    if not _rule_matches(tags, rule_match):
        return None

    if feature.name in {"crop", "grass"} and _excluded_from_vegetation(tags, config):
        return None

    attrs: dict[str, Any] = {}
    for k, v in set_attrs.items():
        attrs[k] = v
    for key in retain:
        val = raw_tags.get(key) or raw_tags.get(key.lower())
        if val is not None and str(val).strip():
            attrs[key] = str(val)
    if derive_ft:
        attrs["forest_type"] = derive_forest_type(tags, config.forest_type)
        name = raw_tags.get("name")
        if name:
            attrs["name"] = str(name)

    return ClassifiedFeature(
        feature_class=feature.name,
        attributes=attrs,
        priority=feature.priority,
    )


def classify_tags(
    tags: dict[str, Any],
    config: MappingsConfig,
    *,
    geometry_type: str,
) -> ClassifiedFeature | None:
    """
    Classify OSM tags into a single Outdoors feature class.

    When multiple rules match, the lowest priority number wins
    (beach/sand/wetland/park beat generic vegetation).
    """
    candidates: list[ClassifiedFeature] = []
    is_point = geometry_type in {"Point", "MultiPoint"}

    for feature in config.features.values():
        if is_point:
            # Points only for layers that explicitly accept point tags
            # (woodland nodes) — marshland/woodland points from polygons
            # are produced later via centroids.
            if not feature.accept_point_tags:
                continue
            if "Point" not in feature.geometry_types and not feature.accept_point_tags:
                continue
        elif geometry_type not in feature.geometry_types:
            continue

        for rule in feature.rules:
            classified = _apply_rule(
                feature,
                rule.match,
                rule.set_attrs,
                rule.retain,
                rule.derive_forest_type,
                tags,
                config,
            )
            if classified is not None:
                candidates.append(classified)
                break  # one rule per feature class is enough

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c.priority, c.feature_class))
    return candidates[0]


def matches_any_feature(tags: dict[str, Any], config: MappingsConfig) -> bool:
    return classify_tags(tags, config, geometry_type="Polygon") is not None
