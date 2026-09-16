# Outdoors OSM landcover — detailed notes

## Purpose

Provide a production-ready, global replacement for Copernicus-based Outdoors landcover (grass, crop, woodland) while unifying existing OSM-derived classes (beach, sand, marshland, park) under one ingestion path.

## Source strategy

- **OSM provider:** Geofabrik (default), abstracted via `ExtractProvider`.
- **CLMS (Europe only):** High Resolution Layers at 10 m — forest type, grassland, crop types — via Copernicus Data Space OData. **Not CLC/CORINE.** OSM and CLMS downloads/polygonize run in the same thread pool.
- **Country:** ISO or name → one or more `path` keys → `{base}/{path}-latest.osm.pbf`.
- **Global:** `global_partitions` continental keys; CLMS runs for the `europe` partition.
- **Resume:** `{scope}__resume.json` lists completed `unit_id`s; `--resume` skips them.
- **Local/offline:** `--local-root` + `--offline` for CI and air-gapped runs. `--clms-local-root` for local HRL GeoTIFFs.

CLMS wins a layer/profile when `native_resolution_m` (10) is strictly smaller than that profile's `simplify_tolerance_m`. Otherwise OSM is kept. If CLMS tiles are missing, the pipeline logs a warning and continues with OSM.

## Deduplication

Stable `id = sha1(feature_class:osm_id:wkt)[:16]`. Features already seen across extract units are counted as `deduplicated` and dropped.

## Generalisation

Projection-aware area filter (`crs_for_area`, default EPSG:3857), topology-preserving simplify, optional dissolve of same-attribute crop/grass at z0–9. Invalid geometries repaired with `shapely.make_valid`.

## Adding a country

Add an entry under `countries:` in `config/countries.yaml` with `name` and `extracts`, plus optional `aliases`.
