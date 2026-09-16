"""Source providers."""

from outdoors_landcover.sources.clms import ClmsHrlProvider
from outdoors_landcover.sources.geofabrik import GeofabrikProvider
from outdoors_landcover.sources.local import LocalProvider
from outdoors_landcover.sources.provider import ExtractProvider, SourceExtract

__all__ = [
    "ClmsHrlProvider",
    "ExtractProvider",
    "GeofabrikProvider",
    "LocalProvider",
    "SourceExtract",
]
