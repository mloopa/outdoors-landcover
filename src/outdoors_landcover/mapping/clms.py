"""CLMS High Resolution Layer config and class-value mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from outdoors_landcover.exceptions import ConfigurationError


@dataclass(frozen=True)
class ClmsLayerConfig:
    name: str
    outdoors_class: str
    dataset_identifier: str
    nodata: frozenset[int]
    classes: dict[int, dict[str, str]]
    default_attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class ClmsConfig:
    version: int
    enabled: bool
    native_resolution_m: float
    year: int
    polygonize_resolution_m: float
    attribution: str
    licence: str
    catalogue_url: str
    token_url: str
    download_url: str
    europe_bbox: tuple[float, float, float, float]
    layers: dict[str, ClmsLayerConfig]
    country_bboxes: dict[str, tuple[float, float, float, float]]


def load_clms_config(path: Path) -> ClmsConfig:
    if not path.is_file():
        raise ConfigurationError(f"CLMS config not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigurationError(f"CLMS config must be a mapping: {path}")

    layers: dict[str, ClmsLayerConfig] = {}
    for name, raw in (data.get("layers") or {}).items():
        layers[name] = ClmsLayerConfig(
            name=name,
            outdoors_class=str(raw.get("outdoors_class") or name),
            dataset_identifier=str(raw["dataset_identifier"]),
            nodata=frozenset(int(v) for v in (raw.get("nodata") or [])),
            classes={
                int(k): {str(ak): str(av) for ak, av in (attrs or {}).items()}
                for k, attrs in (raw.get("classes") or {}).items()
            },
            default_attrs={str(k): str(v) for k, v in (raw.get("default_attrs") or {}).items()},
        )

    bboxes: dict[str, tuple[float, float, float, float]] = {}
    for iso, raw_bb in (data.get("country_bboxes") or {}).items():
        if not isinstance(raw_bb, (list, tuple)) or len(raw_bb) != 4:
            raise ConfigurationError(f"CLMS country_bboxes.{iso} must be [w, s, e, n]")
        bboxes[str(iso).upper()] = (
            float(raw_bb[0]),
            float(raw_bb[1]),
            float(raw_bb[2]),
            float(raw_bb[3]),
        )

    europe = data.get("europe_bbox") or [-24.6, 34.5, 45.0, 71.6]
    return ClmsConfig(
        version=int(data.get("version") or 1),
        enabled=bool(data.get("enabled", True)),
        native_resolution_m=float(data.get("native_resolution_m") or 10),
        year=int(data.get("year") or 2021),
        polygonize_resolution_m=float(data.get("polygonize_resolution_m") or 20),
        attribution=str(
            data.get("attribution") or "© European Union, Copernicus Land Monitoring Service"
        ),
        licence=str(data.get("licence") or "Copernicus Land Monitoring Service"),
        catalogue_url=str(
            data.get("catalogue_url") or "https://catalogue.dataspace.copernicus.eu/odata/v1"
        ).rstrip("/"),
        token_url=str(
            data.get("token_url")
            or (
                "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
                "protocol/openid-connect/token"
            )
        ),
        download_url=str(
            data.get("download_url") or "https://download.dataspace.copernicus.eu/odata/v1"
        ).rstrip("/"),
        europe_bbox=(float(europe[0]), float(europe[1]), float(europe[2]), float(europe[3])),
        layers=layers,
        country_bboxes=bboxes,
    )


def attributes_for_value(layer: ClmsLayerConfig, value: int) -> dict[str, str] | None:
    """Map a raster class value to Outdoors attributes, or None if nodata/unmapped."""
    ival = int(value)
    if ival in layer.nodata:
        return None
    if ival in layer.classes:
        return dict(layer.classes[ival])
    if layer.default_attrs:
        return dict(layer.default_attrs)
    return None


def bbox_for_scope(
    config: ClmsConfig,
    *,
    iso: str | None,
    extract_keys: tuple[str, ...],
) -> tuple[float, float, float, float] | None:
    """Return WGS84 bbox if this scope is pan-European CLMS coverage."""
    if iso and iso.upper() in config.country_bboxes:
        return config.country_bboxes[iso.upper()]
    if any(key == "europe" or key.startswith("europe/") for key in extract_keys):
        if iso:
            return None
        return config.europe_bbox
    return None
