"""Mapping package."""

from outdoors_landcover.mapping.clms import load_clms_config
from outdoors_landcover.mapping.forest import derive_forest_type
from outdoors_landcover.mapping.loader import load_generalisation, load_mappings
from outdoors_landcover.mapping.tags import classify_tags

__all__ = [
    "classify_tags",
    "derive_forest_type",
    "load_clms_config",
    "load_generalisation",
    "load_mappings",
]
