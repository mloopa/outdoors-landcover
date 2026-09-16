"""Unit tests for tag → feature mapping and priority."""

from __future__ import annotations

from helpers import CONFIG

from outdoors_landcover.mapping.loader import load_mappings
from outdoors_landcover.mapping.tags import classify_tags


def _cfg():
    return load_mappings(CONFIG / "mappings.yaml")


def test_beach_preserves_name():
    c = classify_tags({"natural": "beach", "name": "X"}, _cfg(), geometry_type="Polygon")
    assert c is not None
    assert c.feature_class == "beach"
    assert c.attributes["name"] == "X"


def test_sand_sets_natural():
    c = classify_tags({"natural": "sand"}, _cfg(), geometry_type="Polygon")
    assert c is not None
    assert c.feature_class == "sand"
    assert c.attributes["natural"] == "sand"


def test_marshland_wetland():
    c = classify_tags({"natural": "wetland"}, _cfg(), geometry_type="Polygon")
    assert c is not None
    assert c.feature_class == "marshland"
    assert c.attributes["type"] == "wetland"


def test_park_leisure():
    c = classify_tags({"leisure": "park"}, _cfg(), geometry_type="Polygon")
    assert c is not None
    assert c.feature_class == "park"
    assert c.attributes["landuse"] == "park"


def test_crop_farmland_only():
    cfg = _cfg()
    crop = classify_tags({"landuse": "farmland"}, cfg, geometry_type="Polygon")
    assert crop is not None and crop.feature_class == "crop"
    assert classify_tags({"landuse": "plant_nursery"}, cfg, geometry_type="Polygon") is None


def test_orchard_and_vineyard_separate_layers():
    cfg = _cfg()
    orchard = classify_tags(
        {"landuse": "orchard", "name": "Jabłonki"},
        cfg,
        geometry_type="Polygon",
    )
    vineyard = classify_tags({"landuse": "vineyard"}, cfg, geometry_type="Polygon")
    assert orchard is not None
    assert orchard.feature_class == "orchard"
    assert orchard.attributes["landuse"] == "orchard"
    assert orchard.attributes["name"] == "Jabłonki"
    assert vineyard is not None
    assert vineyard.feature_class == "vineyard"
    assert vineyard.attributes["landuse"] == "vineyard"


def test_grass_meadow():
    c = classify_tags({"landuse": "meadow"}, _cfg(), geometry_type="Polygon")
    assert c is not None
    assert c.feature_class == "grass"
    assert c.attributes["landcover"] == "grass"


def test_priority_beach_over_grass():
    c = classify_tags(
        {"natural": "beach", "landuse": "grass"},
        _cfg(),
        geometry_type="Polygon",
    )
    assert c is not None
    assert c.feature_class == "beach"


def test_missing_tags_rejected():
    assert classify_tags({"highway": "primary"}, _cfg(), geometry_type="Polygon") is None


def test_tree_point_not_woodland():
    assert classify_tags({"natural": "tree"}, _cfg(), geometry_type="Point") is None


def test_wood_node_is_woodland_point_candidate():
    c = classify_tags({"natural": "wood"}, _cfg(), geometry_type="Point")
    assert c is not None
    assert c.feature_class == "woodland"
