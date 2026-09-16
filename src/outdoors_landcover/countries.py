"""Country / ISO resolution to Geofabrik extracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from outdoors_landcover.exceptions import ConfigurationError, UnsupportedCountryError


@dataclass(frozen=True)
class CountryResolution:
    iso: str
    name: str
    extracts: tuple[str, ...]


@dataclass
class CountriesCatalog:
    version: int
    provider: str
    base_url: str
    global_partitions: tuple[str, ...]
    countries: dict[str, CountryResolution]
    aliases: dict[str, str]


def load_countries(path: Path) -> CountriesCatalog:
    if not path.is_file():
        raise ConfigurationError(f"Countries config not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)

    countries: dict[str, CountryResolution] = {}
    for iso, raw in (data.get("countries") or {}).items():
        code = str(iso).upper()
        countries[code] = CountryResolution(
            iso=code,
            name=str(raw.get("name") or code),
            extracts=tuple(raw.get("extracts") or []),
        )

    aliases = {
        str(k).strip().lower(): str(v).upper() for k, v in (data.get("aliases") or {}).items()
    }
    # Also index by official name
    for code, country in countries.items():
        aliases[country.name.strip().lower()] = code
        aliases[code.lower()] = code

    return CountriesCatalog(
        version=int(data.get("version") or 1),
        provider=str(data.get("provider") or "geofabrik"),
        base_url=str(data.get("base_url") or "https://download.geofabrik.de"),
        global_partitions=tuple(data.get("global_partitions") or []),
        countries=countries,
        aliases=aliases,
    )


def resolve_country(query: str, catalog: CountriesCatalog) -> CountryResolution:
    """Resolve a country name or ISO-3166-1 alpha-2 code."""
    key = query.strip()
    if not key:
        raise UnsupportedCountryError("Empty country query")

    upper = key.upper()
    if upper in catalog.countries:
        return catalog.countries[upper]

    iso = catalog.aliases.get(key.lower())
    if iso and iso in catalog.countries:
        return catalog.countries[iso]

    # Fuzzy: startswith on names
    matches = [
        c
        for c in catalog.countries.values()
        if c.name.lower().startswith(key.lower()) or key.lower() in c.name.lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(sorted(f"{m.name} ({m.iso})" for m in matches[:8]))
        raise UnsupportedCountryError(
            f"Ambiguous country '{query}'. Candidates: {names}"
        )

    raise UnsupportedCountryError(
        f"Unsupported country '{query}'. Use an ISO code (e.g. IE, PL) or a known name."
    )


def resolve_scope(
    scope: str,
    country: str | None,
    catalog: CountriesCatalog,
) -> tuple[str, tuple[str, ...], CountryResolution | None]:
    """
    Return (scope_label, extract_paths, country_or_none).

    scope: 'country' | 'global'
    """
    scope_l = scope.strip().lower()
    if scope_l == "global":
        if not catalog.global_partitions:
            raise ConfigurationError("No global_partitions defined in countries config")
        return "global", catalog.global_partitions, None
    if scope_l == "country":
        if not country:
            raise UnsupportedCountryError("--country is required when --scope country")
        resolved = resolve_country(country, catalog)
        if not resolved.extracts:
            raise UnsupportedCountryError(
                f"Country {resolved.name} ({resolved.iso}) has no configured extracts"
            )
        return resolved.iso, resolved.extracts, resolved
    raise ConfigurationError(f"Unsupported scope '{scope}'. Use 'country' or 'global'.")


def is_europe_coverage(extract_keys: tuple[str, ...]) -> bool:
    """True when Geofabrik extract keys include the pan-European CLMS footprint."""
    return any(key == "europe" or key.startswith("europe/") for key in extract_keys)
