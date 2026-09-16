"""Resume state for chunked/global processing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ResumeState:
    path: Path
    completed_units: set[str] = field(default_factory=set)
    failed_units: dict[str, str] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> ResumeState:
        if not path.is_file():
            return cls(path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            completed_units=set(data.get("completed_units") or []),
            failed_units=dict(data.get("failed_units") or {}),
            meta=dict(data.get("meta") or {}),
        )

    def mark_completed(self, unit_id: str) -> None:
        self.completed_units.add(unit_id)
        self.failed_units.pop(unit_id, None)
        self.save()

    def mark_failed(self, unit_id: str, error: str) -> None:
        self.failed_units[unit_id] = error
        self.save()

    def is_completed(self, unit_id: str) -> bool:
        return unit_id in self.completed_units

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "completed_units": sorted(self.completed_units),
            "failed_units": self.failed_units,
            "meta": self.meta,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
