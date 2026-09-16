"""Source extract provider abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceExtract:
    """A single downloadable/processable OSM unit."""

    unit_id: str
    path_key: str  # e.g. europe/poland
    url: str | None
    local_path: Path | None = None
    licence: str = "ODbL 1.0"
    attribution: str = "© OpenStreetMap contributors"


class ExtractProvider(ABC):
    """Obtain OSM extracts for processing units."""

    @abstractmethod
    def plan(self, extract_keys: tuple[str, ...]) -> list[SourceExtract]:
        """Return planned extracts without downloading."""

    @abstractmethod
    def ensure_local(self, extract: SourceExtract) -> Path:
        """Download/cache extract and return local path."""
