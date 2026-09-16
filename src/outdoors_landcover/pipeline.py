"""End-to-end landcover ingestion pipeline."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from outdoors_landcover.config import Settings, get_settings
from outdoors_landcover.countries import CountriesCatalog, load_countries, resolve_scope
from outdoors_landcover.exceptions import ConfigurationError, ExtractUnavailableError
from outdoors_landcover.extract.reader import iter_source_features
from outdoors_landcover.generalise import generalise_gdf
from outdoors_landcover.logging import get_logger
from outdoors_landcover.mapping.clms import bbox_for_scope, load_clms_config
from outdoors_landcover.mapping.loader import (
    GeneralisationConfig,
    MappingsConfig,
    load_generalisation,
    load_mappings,
)
from outdoors_landcover.mapping.tags import classify_tags
from outdoors_landcover.merge import select_source_rows
from outdoors_landcover.output import (
    LAYER_ATTR_ORDER,
    build_metadata,
    layer_filename,
    mapbox_style_spec,
    write_attribution,
    write_combined_landcover,
    write_layer,
    write_metadata,
)
from outdoors_landcover.resume import ResumeState
from outdoors_landcover.sources.clms import ClmsHrlProvider, clms_plan_record
from outdoors_landcover.sources.geofabrik import GeofabrikProvider
from outdoors_landcover.sources.local import LocalProvider
from outdoors_landcover.sources.provider import ExtractProvider, SourceExtract
from outdoors_landcover.transform.geometry import (
    area_km2_geodesic,
    is_supported_point,
    is_supported_polygon,
    repair_geometry,
    stable_feature_id,
)

logger = get_logger(__name__)


@dataclass
class UnitStats:
    input: int = 0
    accepted: int = 0
    rejected: int = 0
    deduplicated: int = 0
    emitted: int = 0
    by_class: dict[str, int] = field(default_factory=dict)


@dataclass
class PipelineResult:
    dry_run: bool
    scope: str
    scope_label: str
    planned_extracts: list[SourceExtract]
    outputs: list[Path]
    metadata_path: Path | None
    stats: dict[str, Any]


def _provider_from_settings(
    settings: Settings,
    *,
    local_root: Path | None = None,
    offline: bool = False,
) -> ExtractProvider:
    if local_root is not None:
        return LocalProvider(local_root)
    if settings.provider == "geofabrik":
        return GeofabrikProvider(
            cache_dir=settings.cache_dir,
            base_url=settings.base_url,
            min_free_bytes=settings.min_free_bytes,
            offline=offline,
        )
    if settings.provider == "local":
        return LocalProvider(settings.cache_dir)
    raise ConfigurationError(f"Unknown provider: {settings.provider}")


def _empty_buckets() -> dict[str, list[dict[str, Any]]]:
    return {
        "beach": [],
        "sand": [],
        "marshland": [],
        "park": [],
        "orchard": [],
        "vineyard": [],
        "crop": [],
        "grass": [],
        "woodland": [],
        "woodland_point": [],
    }


def _ingest_unit(
    path: Path,
    mappings: MappingsConfig,
    *,
    seen_ids: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], UnitStats]:
    buckets = _empty_buckets()
    stats = UnitStats()

    for tags, geom in iter_source_features(path):
        stats.input += 1
        geom_type = geom.geom_type
        if is_supported_polygon(geom):
            classified = classify_tags(tags, mappings, geometry_type=geom_type)
        elif is_supported_point(geom):
            classified = classify_tags(tags, mappings, geometry_type="Point")
        else:
            stats.rejected += 1
            continue

        if classified is None:
            stats.rejected += 1
            continue

        repaired = repair_geometry(geom) if is_supported_polygon(geom) else geom
        if repaired is None or repaired.is_empty:
            stats.rejected += 1
            continue

        osm_id = tags.get("osm_id") or tags.get("id") or ""
        fid = stable_feature_id(osm_id, classified.feature_class, repaired)
        if fid in seen_ids:
            stats.deduplicated += 1
            continue
        seen_ids.add(fid)

        attrs = dict(classified.attributes)
        attrs["id"] = fid
        attrs["osm_id"] = str(osm_id) if osm_id != "" else fid
        attrs["geometry"] = repaired

        if classified.feature_class == "marshland" and is_supported_polygon(repaired):
            attrs["area_km2"] = round(area_km2_geodesic(repaired), 6)
            attrs.setdefault("type", "wetland")

        if classified.feature_class == "woodland" and is_supported_point(repaired):
            buckets["woodland_point"].append(attrs)
            stats.accepted += 1
            stats.by_class["woodland_point"] = stats.by_class.get("woodland_point", 0) + 1
            continue

        buckets[classified.feature_class].append(attrs)
        stats.accepted += 1
        stats.by_class[classified.feature_class] = (
            stats.by_class.get(classified.feature_class, 0) + 1
        )

    return buckets, stats


def _unit_cache_dir(temp_dir: Path, scope_label: str) -> Path:
    path = temp_dir / "units" / scope_label
    path.mkdir(parents=True, exist_ok=True)
    return path


def _serialize_buckets(buckets: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for cls, rows in buckets.items():
        serialized = []
        for row in rows:
            item = {k: v for k, v in row.items() if k != "geometry"}
            item["geometry"] = mapping(row["geometry"])
            serialized.append(item)
        out[cls] = serialized
    return out


def _deserialize_buckets(
    raw: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for cls, rows in raw.items():
        restored = []
        for row in rows:
            item = dict(row)
            item["geometry"] = shape(row["geometry"])
            restored.append(item)
        out[cls] = restored
    return out


def _save_unit_result(
    path: Path,
    *,
    unit_id: str,
    buckets: dict[str, list[dict[str, Any]]],
    stats: UnitStats,
    local_path: Path,
) -> None:
    payload = {
        "unit_id": unit_id,
        "local_path": str(local_path),
        "stats": asdict(stats),
        "buckets": _serialize_buckets(buckets),
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _load_unit_result(path: Path) -> tuple[str, dict[str, list[dict[str, Any]]], UnitStats, Path]:
    data = json.loads(path.read_text(encoding="utf-8"))
    stats_raw = data.get("stats") or {}
    stats = UnitStats(
        input=int(stats_raw.get("input") or 0),
        accepted=int(stats_raw.get("accepted") or 0),
        rejected=int(stats_raw.get("rejected") or 0),
        deduplicated=int(stats_raw.get("deduplicated") or 0),
        emitted=int(stats_raw.get("emitted") or 0),
        by_class=dict(stats_raw.get("by_class") or {}),
    )
    return (
        str(data["unit_id"]),
        _deserialize_buckets(data.get("buckets") or {}),
        stats,
        Path(data.get("local_path") or ""),
    )


def _to_gdf(rows: list[dict[str, Any]]) -> gpd.GeoDataFrame:
    if not rows:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame(rows, crs="EPSG:4326")


def _emit_point_layer_from_polygons(
    polygon_rows: list[dict[str, Any]],
    *,
    min_area_km2: float,
    keep_attrs: list[str],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in polygon_rows:
        area = float(row.get("area_km2") or area_km2_geodesic(row["geometry"]))
        if area < min_area_km2:
            continue
        geom: BaseGeometry = row["geometry"]
        centroid = geom.centroid
        point = {k: row[k] for k in keep_attrs if k in row}
        point["id"] = f"pt_{row['id']}"
        point["area_km2"] = round(area, 6)
        point["geometry"] = centroid
        points.append(point)
    return points


def _generalise_and_write(
    buckets: dict[str, list[dict[str, Any]]],
    *,
    mappings: MappingsConfig,
    generalisation: GeneralisationConfig,
    output_dir: Path,
    scope_label: str,
    clms_buckets: dict[str, list[dict[str, Any]]] | None = None,
    clms_native_resolution_m: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    outputs: list[dict[str, Any]] = []
    emitted: dict[str, int] = {}

    # Point layers stay OSM-derived (z11+ simplify is finer than 10 m HRL).
    marsh_cfg = mappings.features["marshland"]
    if marsh_cfg.point_layer:
        marsh_points = _emit_point_layer_from_polygons(
            buckets.get("marshland", []),
            min_area_km2=float(marsh_cfg.min_area_km2_for_point or 0.05),
            keep_attrs=["type"],
        )
        buckets.setdefault("marshland_point", []).extend(marsh_points)

    wood_cfg = mappings.features["woodland"]
    if wood_cfg.point_layer:
        wood_poly_points = _emit_point_layer_from_polygons(
            buckets.get("woodland", []),
            min_area_km2=float(wood_cfg.min_area_km2_for_point or 0.1),
            keep_attrs=["forest_type"],
        )
        buckets.setdefault("woodland_point", []).extend(wood_poly_points)

    for logical, profile_names in generalisation.layer_profiles.items():
        tile_name = generalisation.tile_layer_names.get(logical, f"landcover_{logical}")
        attr_cols = [c for c in LAYER_ATTR_ORDER.get(logical, []) if c not in {"id", "area_km2"}]

        for profile_name in profile_names:
            profile = generalisation.profiles[profile_name]
            rows, source_name = select_source_rows(
                logical=logical,
                profile=profile,
                osm_rows=buckets.get(logical, []),
                clms_rows=(clms_buckets or {}).get(logical),
                native_resolution_m=float(clms_native_resolution_m or 10),
            )
            gdf = _to_gdf(rows)
            if logical.endswith("_point") or gdf.empty or (
                not gdf.empty and gdf.geometry.iloc[0].geom_type in {"Point", "MultiPoint"}
            ):
                generalised = gdf.copy()
                if "id" in generalised.columns and not generalised.empty:
                    generalised = generalised.sort_values("id").reset_index(drop=True)
            else:
                generalised = generalise_gdf(
                    gdf,
                    profile,
                    generalisation,
                    attribute_cols=attr_cols,
                )

            out_gpkg = output_dir / layer_filename(logical, profile_name, scope_label, "gpkg")
            out_geojson = output_dir / layer_filename(
                logical, profile_name, scope_label, "geojson"
            )
            write_layer(generalised, out_gpkg, layer_name=tile_name, logical=logical)
            write_layer(generalised, out_geojson, layer_name=tile_name, logical=logical)
            outputs.append(
                {
                    "logical": logical,
                    "profile": profile_name,
                    "tile_layer": tile_name,
                    "path": str(out_gpkg),
                    "geojson": str(out_geojson),
                    "feature_count": int(len(generalised)),
                    "source": source_name,
                }
            )
            emitted[f"{logical}:{profile_name}"] = int(len(generalised))

    return outputs, emitted


def plan_pipeline(
    *,
    scope: str,
    country: str | None,
    settings: Settings,
    catalog: CountriesCatalog,
    provider: ExtractProvider,
) -> dict[str, Any]:
    scope_label, extracts, resolved = resolve_scope(scope, country, catalog)
    planned = provider.plan(extracts)
    mappings = load_mappings(settings.resolved_mappings_path())
    generalisation = load_generalisation(settings.resolved_generalisation_path())
    clms_cfg = load_clms_config(settings.resolved_clms_path())
    iso = None if resolved is None else resolved.iso
    clms_bbox = bbox_for_scope(clms_cfg, iso=iso, extract_keys=extracts)
    clms_used = bool(clms_cfg.enabled and settings.clms_enabled and clms_bbox)
    clms_reason = (
        "Europe coverage; CLMS HRL 10 m is finer than OSM low/mid-zoom generalisation"
        if clms_used
        else "outside CLMS Europe footprint or CLMS disabled"
    )
    return {
        "scope": scope,
        "scope_label": scope_label,
        "country": None
        if resolved is None
        else {"iso": resolved.iso, "name": resolved.name, "extracts": list(resolved.extracts)},
        "extracts": [
            {
                "unit_id": e.unit_id,
                "path_key": e.path_key,
                "url": e.url,
                "cached": bool(e.local_path and e.local_path.is_file()),
            }
            for e in planned
        ],
        "clms": clms_plan_record(
            clms_cfg,
            bbox=clms_bbox,
            year=settings.clms_year or clms_cfg.year,
            used=clms_used,
            reason=clms_reason,
        ),
        "mappings": str(settings.resolved_mappings_path()),
        "generalisation": str(settings.resolved_generalisation_path()),
        "feature_classes": sorted(mappings.features.keys()),
        "layer_profiles": generalisation.layer_profiles,
        "output_dir": str(settings.output_dir),
        "cache_dir": str(settings.cache_dir),
        "concurrency": settings.concurrency,
        "attribution": mappings.attribution,
        "licence": mappings.licence,
        "parallel": ["osm_extracts", "clms_hrl_layers"],
    }


def run_pipeline(
    *,
    scope: str,
    country: str | None = None,
    dry_run: bool = False,
    resume: bool = False,
    settings: Settings | None = None,
    local_root: Path | None = None,
    offline: bool = False,
) -> PipelineResult:
    settings = settings or get_settings()
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    catalog = load_countries(settings.resolved_countries_path())
    mappings = load_mappings(settings.resolved_mappings_path())
    generalisation = load_generalisation(settings.resolved_generalisation_path())
    clms_cfg = load_clms_config(settings.resolved_clms_path())
    provider = _provider_from_settings(settings, local_root=local_root, offline=offline)

    scope_label, extract_keys, resolved = resolve_scope(scope, country, catalog)
    planned = provider.plan(extract_keys)

    if dry_run:
        plan = plan_pipeline(
            scope=scope,
            country=country,
            settings=settings,
            catalog=catalog,
            provider=provider,
        )
        plan_path = settings.output_dir / f"{scope_label}__dry_run_plan.json"
        write_metadata(plan_path, plan)
        logger.info("Dry-run plan written to %s", plan_path)
        return PipelineResult(
            dry_run=True,
            scope=scope,
            scope_label=scope_label,
            planned_extracts=planned,
            outputs=[plan_path],
            metadata_path=plan_path,
            stats={"planned_units": len(planned)},
        )

    resume_path = settings.output_dir / f"{scope_label}__resume.json"
    if resume:
        state = ResumeState.load(resume_path)
    else:
        state = ResumeState(path=resume_path)
        # Fresh run: clear unit cache for this scope
        unit_dir = _unit_cache_dir(settings.temp_dir, scope_label)
        for stale in unit_dir.glob("*.json"):
            stale.unlink()
    state.path = resume_path
    state.meta = {
        "scope": scope,
        "scope_label": scope_label,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    state.save()

    unit_dir = _unit_cache_dir(settings.temp_dir, scope_label)
    buckets = _empty_buckets()
    seen_ids: set[str] = set()
    total_stats = UnitStats()
    extract_meta: list[dict[str, Any]] = []

    def process_one(extract: SourceExtract) -> tuple[str, dict, UnitStats, Path]:
        local = provider.ensure_local(extract)
        unit_buckets, stats = _ingest_unit(local, mappings, seen_ids=set())
        cache_file = unit_dir / f"{extract.unit_id}.json"
        _save_unit_result(
            cache_file,
            unit_id=extract.unit_id,
            buckets=unit_buckets,
            stats=stats,
            local_path=local,
        )
        return extract.unit_id, unit_buckets, stats, local

    pending = [e for e in planned if not (resume and state.is_completed(e.unit_id))]
    skipped = len(planned) - len(pending)
    if skipped:
        logger.info("Resume: skipping %s completed units", skipped)

    results: list[tuple[str, dict, UnitStats, Path]] = []

    # Reload persisted results for completed units when resuming
    if resume:
        for extract in planned:
            if not state.is_completed(extract.unit_id):
                continue
            cache_file = unit_dir / f"{extract.unit_id}.json"
            if cache_file.is_file():
                results.append(_load_unit_result(cache_file))
            else:
                logger.warning(
                    "Resume: unit %s marked complete but cache missing; reprocessing",
                    extract.unit_id,
                )
                pending.append(extract)

    iso = None if resolved is None else resolved.iso
    clms_bbox = bbox_for_scope(clms_cfg, iso=iso, extract_keys=extract_keys)
    clms_local = settings.clms_local_root
    want_clms = bool(clms_cfg.enabled and settings.clms_enabled and clms_bbox)
    clms_buckets: dict[str, list[dict[str, Any]]] | None = None
    clms_meta: dict[str, Any] | None = None

    def run_clms() -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
        assert clms_bbox is not None
        clms_provider = ClmsHrlProvider(
            clms_cfg,
            cache_dir=settings.cache_dir,
            local_root=clms_local,
            offline=offline,
            username=settings.cdse_username,
            password=settings.cdse_password,
            min_free_bytes=settings.min_free_bytes,
            concurrency=settings.concurrency,
            year=settings.clms_year or clms_cfg.year,
        )
        return clms_provider.ingest(clms_bbox)

    workers = max(1, settings.concurrency)
    run_parallel = workers > 1 and (len(pending) > 1 or (want_clms and pending))

    if run_parallel:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures: dict[Any, Any] = {pool.submit(process_one, e): e for e in pending}
            clms_future = pool.submit(run_clms) if want_clms else None
            for fut in as_completed(list(futures)):
                extract = futures[fut]
                try:
                    results.append(fut.result())
                    state.mark_completed(extract.unit_id)
                except Exception as exc:  # noqa: BLE001
                    state.mark_failed(extract.unit_id, str(exc))
                    raise
            if clms_future is not None:
                try:
                    clms_buckets, clms_meta = clms_future.result()
                except ExtractUnavailableError as exc:
                    logger.warning("CLMS HRL skipped: %s", exc)
    else:
        for extract in pending:
            try:
                results.append(process_one(extract))
                state.mark_completed(extract.unit_id)
            except Exception as exc:  # noqa: BLE001
                state.mark_failed(extract.unit_id, str(exc))
                raise
        if want_clms:
            try:
                clms_buckets, clms_meta = run_clms()
            except ExtractUnavailableError as exc:
                logger.warning("CLMS HRL skipped: %s", exc)

    # Merge with global dedupe (boundary overlaps between regional extracts)
    for unit_id, unit_buckets, stats, local in sorted(results, key=lambda r: r[0]):
        for cls, rows in unit_buckets.items():
            kept = []
            for row in rows:
                fid = row["id"]
                if fid in seen_ids:
                    total_stats.deduplicated += 1
                    continue
                seen_ids.add(fid)
                kept.append(row)
            buckets.setdefault(cls, []).extend(kept)
        total_stats.input += stats.input
        total_stats.accepted += stats.accepted
        total_stats.rejected += stats.rejected
        for k, v in stats.by_class.items():
            total_stats.by_class[k] = total_stats.by_class.get(k, 0) + v
        extract_meta.append(
            {
                "unit_id": unit_id,
                "local_path": str(local),
                "input": stats.input,
                "accepted": stats.accepted,
                "rejected": stats.rejected,
            }
        )

    output_infos, emitted = _generalise_and_write(
        buckets,
        mappings=mappings,
        generalisation=generalisation,
        output_dir=settings.output_dir,
        scope_label=scope_label,
        clms_buckets=clms_buckets,
        clms_native_resolution_m=clms_cfg.native_resolution_m if clms_buckets else None,
    )
    total_stats.emitted = sum(emitted.values())

    country_meta = (
        None
        if resolved is None
        else {"iso": resolved.iso, "name": resolved.name, "extracts": list(resolved.extracts)}
    )
    metadata = build_metadata(
        scope=scope,
        scope_label=scope_label,
        country=country_meta,
        extracts=extract_meta,
        mappings=mappings,
        generalisation=generalisation,
        counts={
            "input": total_stats.input,
            "accepted": total_stats.accepted,
            "rejected": total_stats.rejected,
            "deduplicated": total_stats.deduplicated,
            "emitted": total_stats.emitted,
            "by_class": total_stats.by_class,
            "by_layer_profile": emitted,
        },
        outputs=output_infos,
        clms=None
        if not clms_buckets
        else {
            "service": "Copernicus Land Monitoring Service",
            "product_family": "High Resolution Layer (Vegetated Land Cover Characteristics)",
            "not": "CORINE Land Cover (CLC)",
            "native_resolution_m": clms_cfg.native_resolution_m,
            "year": settings.clms_year or clms_cfg.year,
            "attribution": clms_cfg.attribution,
            "licence": clms_cfg.licence,
            "stats": clms_meta,
        },
    )
    metadata_path = settings.output_dir / f"{scope_label}__metadata.json"
    write_metadata(metadata_path, metadata)
    attr_path = settings.output_dir / f"{scope_label}__ATTRIBUTION.txt"
    write_attribution(
        attr_path,
        mappings.attribution,
        mappings.licence,
        extra_lines=(
            [clms_cfg.attribution, f"Data licence: {clms_cfg.licence}"] if clms_buckets else None
        ),
    )

    gpkg_paths = [Path(o["path"]) for o in output_infos]
    poly_gpkgs = [p for p in gpkg_paths if "_point__" not in p.name]
    point_gpkgs = [p for p in gpkg_paths if "_point__" in p.name]
    combined_gpkg = write_combined_landcover(
        poly_gpkgs,
        settings.output_dir / f"{scope_label}__landcover__all.gpkg",
    )
    combined_points = write_combined_landcover(
        point_gpkgs,
        settings.output_dir / f"{scope_label}__landcover_point__all.gpkg",
        layer_name="landcover_point",
    )
    style_path = settings.output_dir / f"{scope_label}__mapbox-style.json"
    style_path.write_text(
        json.dumps(mapbox_style_spec(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    output_paths = list(gpkg_paths)
    output_paths.extend(Path(o["geojson"]) for o in output_infos if o.get("geojson"))
    output_paths.extend([metadata_path, attr_path, style_path])
    if combined_gpkg is not None:
        output_paths.append(combined_gpkg)
    if combined_points is not None:
        output_paths.append(combined_points)
    return PipelineResult(
        dry_run=False,
        scope=scope,
        scope_label=scope_label,
        planned_extracts=planned,
        outputs=output_paths,
        metadata_path=metadata_path,
        stats=metadata["counts"],
    )
