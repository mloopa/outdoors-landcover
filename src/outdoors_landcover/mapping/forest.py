"""Forest type derivation from OSM tags."""

from __future__ import annotations

from outdoors_landcover.mapping.loader import ForestTypeConfig


def _tags_match(tags: dict[str, str], required: dict[str, str]) -> bool:
    return all(str(tags.get(k, "")).lower() == str(v).lower() for k, v in required.items())


def _species_contains(tags: dict[str, str], needles: list[str]) -> bool:
    species = str(tags.get("species") or tags.get("genus") or "").lower()
    if not species:
        return False
    return any(n.lower() in species for n in needles)


def derive_forest_type(tags: dict[str, str], config: ForestTypeConfig) -> str:
    """
    Derive forest_type: coniferous | non_coniferous | unknown.

    Priority: explicit leaf_type / wood:type / forest_type tags, then species
    heuristics, else deterministic fallback.
    """
    for rule in config.coniferous_any_of:
        if _tags_match(tags, {str(k): str(v) for k, v in rule.items()}):
            return "coniferous"
    if _species_contains(tags, config.coniferous_species):
        return "coniferous"

    for rule in config.non_coniferous_any_of:
        if _tags_match(tags, {str(k): str(v) for k, v in rule.items()}):
            return "non_coniferous"
    if _species_contains(tags, config.non_coniferous_species):
        return "non_coniferous"

    return config.fallback
