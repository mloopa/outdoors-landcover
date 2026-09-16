"""Generalisation profile configuration tests."""

from __future__ import annotations

from helpers import CONFIG

from outdoors_landcover.mapping.loader import load_generalisation


def test_profiles_present():
    cfg = load_generalisation(CONFIG / "generalisation.yaml")
    for name in ("z0_9", "z8_11", "z8_12", "z11_14", "z12_plus", "z14_plus", "z11_plus"):
        assert name in cfg.profiles


def test_layer_zoom_contract():
    cfg = load_generalisation(CONFIG / "generalisation.yaml")
    assert cfg.layer_profiles["crop"] == ["z0_9"]
    assert cfg.layer_profiles["grass"] == ["z0_9"]
    assert cfg.layer_profiles["woodland"] == ["z8_11", "z11_14", "z14_plus"]
    assert cfg.layer_profiles["marshland"] == ["z8_12", "z12_plus"]
    assert cfg.layer_profiles["beach"] == ["z11_plus"]
    assert cfg.layer_profiles["orchard"] == ["z11_plus"]
    assert cfg.layer_profiles["vineyard"] == ["z11_plus"]


def test_tile_layer_names_match_logical_features():
    cfg = load_generalisation(CONFIG / "generalisation.yaml")
    for logical, tile_name in cfg.tile_layer_names.items():
        assert tile_name == logical


def test_min_area_increases_with_detail():
    cfg = load_generalisation(CONFIG / "generalisation.yaml")
    assert cfg.profiles["z0_9"].min_area_m2 > cfg.profiles["z14_plus"].min_area_m2
    z8 = cfg.profiles["z8_11"].simplify_tolerance_m
    z14 = cfg.profiles["z14_plus"].simplify_tolerance_m
    assert z8 > z14
