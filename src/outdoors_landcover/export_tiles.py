"""Vector export helpers (GeoJSON + optional tippecanoe MBTiles)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import geopandas as gpd

from outdoors_landcover.logging import get_logger
from outdoors_landcover.output import unify_gdf

logger = get_logger(__name__)

# tippecanoe zoom ranges per generalisation profile
PROFILE_ZOOM: dict[str, tuple[int, int]] = {
    "z0_9": (0, 9),
    "z8_11": (8, 11),
    "z8_12": (8, 12),
    "z11_14": (11, 14),
    "z12_plus": (12, 14),
    "z14_plus": (14, 16),
    "z11_plus": (11, 16),
}


def parse_layer_stem(stem: str) -> tuple[str, str, str] | None:
    """Parse `{scope}__{logical}__{profile}` filenames."""
    parts = stem.split("__")
    if len(parts) < 3:
        return None
    scope, logical, profile = parts[0], parts[1], "__".join(parts[2:])
    return scope, logical, profile


def write_geojson(
    gdf: gpd.GeoDataFrame,
    path: Path,
    *,
    logical: str,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = unify_gdf(gdf, logical)
    if path.exists():
        path.unlink()
    path.write_text(out.to_json(), encoding="utf-8")
    logger.info("Wrote %s features → %s", len(out), path)
    return path


def gpkg_to_geojson(gpkg_path: Path, geojson_path: Path | None = None) -> Path:
    stem_info = parse_layer_stem(gpkg_path.stem)
    logical = stem_info[1] if stem_info else "unknown"
    dest = geojson_path or gpkg_path.with_suffix(".geojson")
    gdf = gpd.read_file(gpkg_path)
    return write_geojson(gdf, dest, logical=logical)


def export_dir_to_geojson(
    input_dir: Path,
    output_dir: Path | None = None,
) -> list[Path]:
    """Convert all layer GPKGs in a directory to sibling/neighbouring GeoJSON files."""
    out_dir = output_dir or (input_dir / "geojson")
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for gpkg in sorted(input_dir.glob("*__*__*.gpkg")):
        dest = out_dir / f"{gpkg.stem}.geojson"
        written.append(gpkg_to_geojson(gpkg, dest))
    return written


def tippecanoe_available() -> bool:
    return shutil.which("tippecanoe") is not None


def _geojson_is_empty(path: Path) -> bool:
    """Fast empty check without loading multi‑hundred‑MB files into memory."""
    try:
        size = path.stat().st_size
    except OSError:
        return True
    if size < 250:
        return True
    # Empty FeatureCollections from Fiona/pyogrio are tiny (~170B).
    # Anything larger is treated as non-empty for tippecanoe.
    return False


def build_mbtiles(
    geojson_paths: list[Path],
    mbtiles_path: Path,
    *,
    attribution: str = "© OpenStreetMap contributors",
    name: str = "outdoors-landcover",
) -> Path:
    """
    Build a multi-layer MBTiles with tippecanoe.

    Each GeoJSON file stem `{scope}__{logical}__{profile}` becomes a tippecanoe
    layer named `{logical}` with zoom range from PROFILE_ZOOM[profile].
    """
    if not tippecanoe_available():
        raise RuntimeError(
            "tippecanoe not found on PATH. Install via: brew install tippecanoe "
            "or build into .tools/tippecanoe"
        )
    if not geojson_paths:
        raise RuntimeError("No GeoJSON inputs for MBTiles")

    mbtiles_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = mbtiles_path.parent / f".tippecanoe_{mbtiles_path.stem}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    partials: list[Path] = []

    for gj in geojson_paths:
        parsed = parse_layer_stem(gj.stem)
        if parsed is None:
            continue
        _scope, logical, profile = parsed
        zmin, zmax = PROFILE_ZOOM.get(profile, (0, 16))
        if _geojson_is_empty(gj):
            logger.info("Skipping empty layer %s", gj.name)
            continue
        part = tmp_dir / f"{gj.stem}.mbtiles"
        part_cmd = [
            "tippecanoe",
            "-q",
            "-o",
            str(part),
            "-f",
            "-l",
            logical,
            "-Z",
            str(zmin),
            "-z",
            str(zmax),
            "-n",
            f"{name}-{logical}-{profile}",
            "-A",
            attribution,
            "--no-tile-size-limit",
            "--detect-shared-borders",
            str(gj),
        ]
        logger.info("tippecanoe %s (z%s–%s, %.1f MB)", gj.name, zmin, zmax, gj.stat().st_size / 1e6)
        subprocess.run(part_cmd, check=True)
        partials.append(part)

    if not partials:
        raise RuntimeError("No non-empty GeoJSON layers to tile")

    if len(partials) == 1:
        shutil.copy2(partials[0], mbtiles_path)
    else:
        tile_join = shutil.which("tile-join")
        if not tile_join:
            raise RuntimeError("tile-join not found (comes with tippecanoe)")
        join_cmd = [
            tile_join,
            "-o",
            str(mbtiles_path),
            "-f",
            "-n",
            name,
            "-A",
            attribution,
        ]
        join_cmd.extend(str(p) for p in partials)
        logger.info("tile-join %s partials → %s", len(partials), mbtiles_path)
        subprocess.run(join_cmd, check=True)

    shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.info("Wrote MBTiles → %s", mbtiles_path)
    return mbtiles_path


def write_tippecanoe_manifest(
    geojson_paths: list[Path],
    manifest_path: Path,
    *,
    mbtiles_name: str = "outdoors-landcover.mbtiles",
) -> Path:
    """Write a shell script the user can run once tippecanoe is installed."""
    lines = [
        "#!/usr/bin/env bash",
        "# Auto-generated tippecanoe recipe for Outdoors landcover layers.",
        "set -euo pipefail",
        'ROOT="$(cd "$(dirname "$0")" && pwd)"',
        "command -v tippecanoe >/dev/null || "
        "{ echo 'Install tippecanoe (brew install tippecanoe)' >&2; exit 1; }",
        "command -v tile-join >/dev/null || { echo 'tile-join missing' >&2; exit 1; }",
        'TMP="$ROOT/.tippecanoe_parts"',
        'rm -rf "$TMP" && mkdir -p "$TMP"',
        "PARTS=()",
    ]
    for gj in geojson_paths:
        parsed = parse_layer_stem(gj.stem)
        if parsed is None:
            continue
        _scope, logical, profile = parsed
        zmin, zmax = PROFILE_ZOOM.get(profile, (0, 16))
        part = f"$TMP/{gj.stem}.mbtiles"
        # Paths relative to manifest dir
        rel = gj.name if gj.parent == manifest_path.parent else str(gj)
        lines.append(
            f'tippecanoe -q -o "{part}" -f -l {logical} -Z {zmin} -z {zmax} '
            f'--no-tile-size-limit --detect-shared-borders '
            f'"$ROOT/{Path(rel).name}"'
        )
        lines.append(f'PARTS+=("{part}")')
    lines.extend(
        [
            f'tile-join -o "$ROOT/{mbtiles_name}" -f -n outdoors-landcover '
            f'-A "© OpenStreetMap contributors" "${{PARTS[@]}}"',
            'rm -rf "$TMP"',
            f'echo "Wrote $ROOT/{mbtiles_name}"',
        ]
    )
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_path.chmod(manifest_path.stat().st_mode | 0o111)
    return manifest_path


def export_summary(
    paths: list[Path], mbtiles: Path | None, manifest: Path | None
) -> dict[str, Any]:
    return {
        "geojson": [str(p) for p in paths],
        "mbtiles": str(mbtiles) if mbtiles else None,
        "tippecanoe_script": str(manifest) if manifest else None,
        "tippecanoe_available": tippecanoe_available(),
    }
