"""Tests for forest_type derivation."""

from __future__ import annotations

from helpers import CONFIG

from outdoors_landcover.mapping.forest import derive_forest_type
from outdoors_landcover.mapping.loader import load_mappings
from outdoors_landcover.mapping.tags import classify_tags


def _ft():
    return load_mappings(CONFIG / "mappings.yaml").forest_type


def test_coniferous_from_leaf_type():
    assert derive_forest_type({"leaf_type": "needleleaved"}, _ft()) == "coniferous"


def test_non_coniferous_from_leaf_type():
    assert derive_forest_type({"leaf_type": "broadleaved"}, _ft()) == "non_coniferous"


def test_coniferous_from_species():
    assert derive_forest_type({"species": "Pinus sylvestris"}, _ft()) == "coniferous"


def test_non_coniferous_from_species():
    assert derive_forest_type({"species": "Quercus robur"}, _ft()) == "non_coniferous"


def test_fallback_unknown():
    assert derive_forest_type({}, _ft()) == "unknown"


def test_classify_sets_forest_type():
    cfg = load_mappings(CONFIG / "mappings.yaml")
    c = classify_tags(
        {"natural": "wood", "leaf_type": "needleleaved", "name": "Pines"},
        cfg,
        geometry_type="Polygon",
    )
    assert c is not None
    assert c.attributes["forest_type"] == "coniferous"
    assert c.attributes["name"] == "Pines"
