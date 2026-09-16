"""Geofabrik OSM extract provider."""

from __future__ import annotations

from pathlib import Path

from outdoors_landcover.exceptions import ExtractUnavailableError
from outdoors_landcover.sources.cache import cache_path_for, download_file, ensure_free_space
from outdoors_landcover.sources.provider import ExtractProvider, SourceExtract


class GeofabrikProvider(ExtractProvider):
    """
    Download regional `.osm.pbf` extracts from Geofabrik.

    URL pattern: {base_url}/{path}-latest.osm.pbf
    e.g. https://download.geofabrik.de/europe/poland-latest.osm.pbf
    """

    def __init__(
        self,
        *,
        cache_dir: Path,
        base_url: str = "https://download.geofabrik.de",
        min_free_bytes: int = 1_000_000_000,
        offline: bool = False,
    ) -> None:
        self.cache_dir = cache_dir
        self.base_url = base_url.rstrip("/")
        self.min_free_bytes = min_free_bytes
        self.offline = offline

    def _url_for(self, path_key: str) -> str:
        return f"{self.base_url}/{path_key}-latest.osm.pbf"

    def plan(self, extract_keys: tuple[str, ...]) -> list[SourceExtract]:
        planned: list[SourceExtract] = []
        for key in extract_keys:
            url = self._url_for(key)
            local = cache_path_for(self.cache_dir, url)
            planned.append(
                SourceExtract(
                    unit_id=key.replace("/", "_"),
                    path_key=key,
                    url=url,
                    local_path=local if local.is_file() else None,
                )
            )
        return planned

    def ensure_local(self, extract: SourceExtract) -> Path:
        assert extract.url is not None
        dest = cache_path_for(self.cache_dir, extract.url)
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        if self.offline:
            raise ExtractUnavailableError(
                f"Extract not in cache and offline mode enabled: {extract.path_key} ({dest})"
            )
        ensure_free_space(self.cache_dir, self.min_free_bytes)
        return download_file(extract.url, dest)
