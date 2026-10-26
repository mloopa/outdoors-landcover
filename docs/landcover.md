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

Projection-aware area filter (`crs_for_area`, default EPSG:3857), topology-preserving simplify. Invalid geometries repaired with `shapely.make_valid`.

Below zoom 11 (woodland z8–9 and z10, crop/grass z0–9, marshland z8–11) profiles set `dissolve_by` and run area generalisation per dissolve group:

1. union all features of the group (woodland ignores `forest_type`/`name`, so neighbouring stands merge);
2. close gaps narrower than `2 × close_gap_m` (buffer out, then in) — roads, rides and narrow clearings disappear;
3. drop parts thinner than `2 × open_width_m` (buffer in, then out) — strips and necks disappear, outlines get rounded;
4. fill holes smaller than `fill_holes_below_m2`;
5. simplify, then drop polygons smaller than `min_area_m2`.

Distances and areas are Web Mercator units, i.e. proportional to screen pixels (≈306 m/px at z8 with 512 px tiles). Work is split into `tile_size_m` tiles that overlap by the morphology radius and are stitched afterwards, so country and continent layers stay within memory. Each tile-profile pair has its own tippecanoe zoom range (`export_tiles.PROFILE_ZOOM`); ranges of the same layer do not overlap.

## Adding a country

Add an entry under `countries:` in `config/countries.yaml` with `name` and `extracts`, plus optional `aliases`.
