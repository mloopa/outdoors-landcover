"""Local GeoJSON / GeoPackage / OSM XML source for fixtures and offline runs."""

from __future__ import annotations

from pathlib import Path

from outdoors_landcover.exceptions import ExtractUnavailableError
from outdoors_landcover.sources.provider import ExtractProvider, SourceExtract


class LocalProvider(ExtractProvider):
    """Map extract keys to local files under a root directory."""

    def __init__(self, root: Path, mapping: dict[str, Path] | None = None) -> None:
        self.root = root
        self.mapping = mapping or {}

    def plan(self, extract_keys: tuple[str, ...]) -> list[SourceExtract]:
        planned: list[SourceExtract] = []
        for key in extract_keys:
            path = self.mapping.get(key) or self._guess(key)
            planned.append(
                SourceExtract(
                    unit_id=key.replace("/", "_"),
                    path_key=key,
                    url=None,
                    local_path=path if path and path.is_file() else None,
                )
            )
        return planned

    def _guess(self, key: str) -> Path | None:
        stem = key.replace("/", "_")
        for ext in (".geojson", ".json", ".gpkg", ".osm", ".osm.pbf"):
            candidate = self.root / f"{stem}{ext}"
            if candidate.is_file():
                return candidate
            candidate = self.root / f"{key.split('/')[-1]}{ext}"
            if candidate.is_file():
                return candidate
        return None

    def ensure_local(self, extract: SourceExtract) -> Path:
        if extract.local_path and extract.local_path.is_file():
            return extract.local_path
        path = self.mapping.get(extract.path_key) or self._guess(extract.path_key)
        if path and path.is_file():
            return path
        raise ExtractUnavailableError(
            f"Local extract not found for {extract.path_key} under {self.root}"
        )
