"""Download cache helpers."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import httpx

from outdoors_landcover.exceptions import ExtractUnavailableError, InsufficientStorageError
from outdoors_landcover.logging import get_logger

logger = get_logger(__name__)


def cache_path_for(cache_dir: Path, url: str, suffix: str = ".osm.pbf") -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    name = url.rstrip("/").split("/")[-1]
    if not name.endswith(suffix):
        name = f"{name}{suffix}"
    return cache_dir / f"{digest}_{name}"


def ensure_free_space(path: Path, required_bytes: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path)
    if usage.free < required_bytes:
        raise InsufficientStorageError(
            f"Insufficient disk space under {path}: "
            f"need ~{required_bytes / 1e9:.1f} GiB free, have {usage.free / 1e9:.1f} GiB"
        )


def download_file(
    url: str,
    dest: Path,
    *,
    timeout: float = 120.0,
    headers: dict[str, str] | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    logger.info("Downloading %s → %s", url, dest)
    try:
        with httpx.stream(
            "GET", url, follow_redirects=True, timeout=timeout, headers=headers
        ) as resp:
            if resp.status_code >= 400:
                raise ExtractUnavailableError(
                    f"Failed to download {url}: HTTP {resp.status_code}"
                )
            with partial.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        partial.replace(dest)
    except httpx.HTTPError as exc:
        if partial.exists():
            partial.unlink(missing_ok=True)
        raise ExtractUnavailableError(f"Failed to download {url}: {exc}") from exc
    return dest
