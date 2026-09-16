"""Command-line interface for Outdoors OSM landcover ingestion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from outdoors_landcover.config import get_settings
from outdoors_landcover.exceptions import OutdoorsLandcoverError
from outdoors_landcover.logging import setup_logging
from outdoors_landcover.pipeline import run_pipeline

app = typer.Typer(
    name="outdoors-landcover",
    help="Global OSM landcover ingestion for the Outdoors map.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Global OSM landcover ingestion for the Outdoors map."""


@app.command("ingest")
def ingest(
    scope: str = typer.Option(
        ...,
        "--scope",
        help="Processing scope: country | global.",
    ),
    country: Optional[str] = typer.Option(
        None,
        "--country",
        help="Country name or ISO-3166-1 alpha-2 (required for country).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate config and show planned work without processing.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Skip units recorded as completed in the resume state file.",
    ),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir",
        help="Directory for layer GPKGs and metadata.",
    ),
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir",
        help="OSM extract download cache.",
    ),
    temp_dir: Optional[Path] = typer.Option(
        None,
        "--temp-dir",
        help="Temporary working directory.",
    ),
    concurrency: Optional[int] = typer.Option(
        None,
        "--concurrency",
        help="Max parallel extract units.",
    ),
    local_root: Optional[Path] = typer.Option(
        None,
        "--local-root",
        help="Use local extracts under this directory instead of downloading (tests/offline).",
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Do not download; fail if extract is not cached.",
    ),
    clms: Optional[bool] = typer.Option(
        None,
        "--clms/--no-clms",
        help="Use CLMS HRL for Europe when 10 m is finer than OSM (default: on).",
    ),
    clms_local_root: Optional[Path] = typer.Option(
        None,
        "--clms-local-root",
        help="Local GeoTIFF/COG directory for CLMS HRL (offline / cached tiles).",
    ),
    clms_year: Optional[int] = typer.Option(
        None,
        "--clms-year",
        help="CLMS HRL reference year (default from config/clms.yaml, typically 2021).",
    ),
    log_level: str = typer.Option("INFO", "--log-level", help="Logging level."),
) -> None:
    """Ingest OSM landcover for a country or worldwide."""
    setup_logging(log_level)
    overrides: dict = {"log_level": log_level}
    if output_dir is not None:
        overrides["output_dir"] = output_dir
    if cache_dir is not None:
        overrides["cache_dir"] = cache_dir
    if temp_dir is not None:
        overrides["temp_dir"] = temp_dir
    if concurrency is not None:
        overrides["concurrency"] = concurrency
    if clms is not None:
        overrides["clms_enabled"] = clms
    if clms_local_root is not None:
        overrides["clms_local_root"] = clms_local_root
    if clms_year is not None:
        overrides["clms_year"] = clms_year
    settings = get_settings(**overrides)

    try:
        result = run_pipeline(
            scope=scope,
            country=country,
            dry_run=dry_run,
            resume=resume,
            settings=settings,
            local_root=local_root,
            offline=offline,
        )
    except OutdoorsLandcoverError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    summary = {
        "dry_run": result.dry_run,
        "scope": result.scope,
        "scope_label": result.scope_label,
        "units": len(result.planned_extracts),
        "outputs": [str(p) for p in result.outputs],
        "stats": result.stats,
        "metadata": str(result.metadata_path) if result.metadata_path else None,
    }
    typer.echo(json.dumps(summary, indent=2))


@app.command("export")
def export_cmd(
    input_dir: Path = typer.Option(
        ...,
        "--input-dir",
        help="Directory with layer GPKG/GeoJSON outputs from ingest.",
    ),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir",
        help="GeoJSON output directory (default: <input-dir>/geojson).",
    ),
    mbtiles: bool = typer.Option(
        False,
        "--mbtiles/--no-mbtiles",
        help="Also build MBTiles with tippecanoe when available.",
    ),
    mbtiles_path: Optional[Path] = typer.Option(
        None,
        "--mbtiles-path",
        help="Target .mbtiles path (default: <geojson-dir>/outdoors-landcover.mbtiles).",
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Export ingest GPKGs to GeoJSON and optionally MBTiles (tippecanoe)."""
    setup_logging(log_level)
    from outdoors_landcover.export_tiles import (
        build_mbtiles,
        export_dir_to_geojson,
        export_summary,
        tippecanoe_available,
        write_tippecanoe_manifest,
    )

    try:
        # Prefer converting GPKGs; if only geojson already present, collect them
        gpkgs = list(input_dir.glob("*__*__*.gpkg"))
        if gpkgs:
            geojson_paths = export_dir_to_geojson(input_dir, output_dir)
        else:
            src = output_dir or input_dir
            geojson_paths = sorted(src.glob("*__*__*.geojson"))
            if not geojson_paths:
                raise OutdoorsLandcoverError(f"No layer GPKG/GeoJSON found in {input_dir}")

        geo_dir = geojson_paths[0].parent
        manifest = write_tippecanoe_manifest(
            geojson_paths,
            geo_dir / "build_mbtiles.sh",
        )
        mbtiles_out: Path | None = None
        if mbtiles:
            if tippecanoe_available():
                target = mbtiles_path or (geo_dir / "outdoors-landcover.mbtiles")
                mbtiles_out = build_mbtiles(geojson_paths, target)
            else:
                typer.secho(
                    "tippecanoe not installed — wrote build_mbtiles.sh instead. "
                    "Install with: brew install tippecanoe",
                    fg=typer.colors.YELLOW,
                    err=True,
                )

        typer.echo(json.dumps(export_summary(geojson_paths, mbtiles_out, manifest), indent=2))
    except OutdoorsLandcoverError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
