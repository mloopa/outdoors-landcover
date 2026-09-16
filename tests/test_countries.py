"""Country / ISO scope resolution tests."""

from __future__ import annotations

import pytest
from helpers import CONFIG

from outdoors_landcover.countries import (
    is_europe_coverage,
    load_countries,
    resolve_country,
    resolve_scope,
)
from outdoors_landcover.exceptions import UnsupportedCountryError


def _catalog():
    return load_countries(CONFIG / "countries.yaml")


def test_resolve_iso():
    c = resolve_country("IE", _catalog())
    assert c.iso == "IE"
    assert "ireland" in c.extracts[0]


def test_resolve_name():
    c = resolve_country("Poland", _catalog())
    assert c.iso == "PL"


def test_resolve_alias():
    assert resolve_country("uk", _catalog()).iso == "GB"


def test_russia_multi_extract():
    c = resolve_country("RU", _catalog())
    assert len(c.extracts) > 1


def test_unsupported():
    with pytest.raises(UnsupportedCountryError):
        resolve_country("Narnia", _catalog())


def test_scope_global():
    label, extracts, country = resolve_scope("global", None, _catalog())
    assert label == "global"
    assert country is None
    assert "europe" in extracts
    assert is_europe_coverage(extracts)
    assert is_europe_coverage(("europe/poland",))
    assert not is_europe_coverage(("asia/japan",))


def test_scope_country_requires_name():
    with pytest.raises(UnsupportedCountryError):
        resolve_scope("country", None, _catalog())
