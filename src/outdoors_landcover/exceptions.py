"""Exceptions for the landcover pipeline."""

from __future__ import annotations


class OutdoorsLandcoverError(Exception):
    """Base error."""


class UnsupportedCountryError(OutdoorsLandcoverError):
    """Country name or ISO code is not supported."""


class ExtractUnavailableError(OutdoorsLandcoverError):
    """Source extract could not be downloaded or located."""


class InsufficientStorageError(OutdoorsLandcoverError):
    """Not enough free disk space for planned downloads/outputs."""


class ConfigurationError(OutdoorsLandcoverError):
    """Invalid or missing configuration."""


class GeometryError(OutdoorsLandcoverError):
    """Geometry validation/repair failure."""
