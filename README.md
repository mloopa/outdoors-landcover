# Outdoors OSM landcover pipeline

Global OpenStreetMap landcover ingestion for the Outdoors map. Replaces the Europe-only Copernicus landcover import with a configurable, resumable OSM baseline that works for a single country or worldwide.

> **Repository note:** This workspace did not contain a pre-existing Outdoors vector-tile codebase. The pipeline was implemented as a standalone project under `outdoors/`, following conventions from the sibling `dem-contours` tool (Typer CLI, pydantic settings, GeoPackage outputs, pytest). Vector-tile layer names match the logical feature names from the product description (`beach`, `crop`, `woodland`, …).

## Features

- Country (name or ISO-3166-1 alpha-2) or `global` scope
- Geofabrik extract provider with download cache and offline/local modes
- Copernicus Land Monitoring Service High Resolution Layers for Europe (10 m forest type, grassland, crop types) fetched in parallel with OSM when they are finer than OSM generalisation
- Version-controlled OSM tag mappings and zoom generalisation profiles
- Priority rules to avoid duplicate rendering across overlapping tags
- Chunked, resumable global processing with bounded concurrency
- GeoPackage layer outputs + JSON metadata + OSM attribution file
- Dry-run mode that prints planned extracts, filters and outputs
- Offline tests with a checked-in GeoJSON fixture

## Installation

Requires Python 3.9+.

```bash
cd outdoors
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Optional: GDAL with OSM driver (or pre-convert PBF → GeoJSON) for reading `.osm.pbf` extracts. Fixture / local GeoJSON workflows need no GDAL OSM support.

## Commands

```bash
# Country by name
outdoors-landcover ingest --scope country --country Ireland

# Country by ISO code
outdoors-landcover ingest --scope country --country IE

# Global (continental Geofabrik partitions)
outdoors-landcover ingest --scope global --concurrency 4

# Dry-run (no download/process)
outdoors-landcover ingest --scope country --country PL --dry-run

# Resume interrupted job
outdoors-landcover ingest --scope country --country PL --resume

# Offline / fixture mode
outdoors-landcover ingest --scope country --country IE \
  --local-root ./tests/fixtures --offline \
  --output-dir ./outputs/landcover

# Europe: OSM + CLMS High Resolution Layers in parallel (default)
outdoors-landcover ingest --scope country --country PL --concurrency 4

# OSM only
outdoors-landcover ingest --scope country --country PL --no-clms
```

Paths (`--cache-dir`, `--temp-dir`, `--output-dir`, `--concurrency`) and `OUTDOORS_*` environment variables are configurable. See `config/settings.example.yaml`.

## Architecture

```text
config/
  mappings.yaml          # OSM tags → feature classes, priority, forest_type
  generalisation.yaml    # Zoom profiles, min area, simplify, tile names
  countries.yaml         # ISO/name → Geofabrik extract path(s)
  clms.yaml              # CLMS HRL datasets, 10 m class maps, Europe bboxes

src/outdoors_landcover/
  cli.py                 # Typer entry: outdoors-landcover ingest
  countries.py           # Scope / ISO resolution
  sources/               # Geofabrik + local + CLMS HRL providers, cache
  extract/               # GeoJSON / GPKG / OSM XML / PBF readers
  mapping/               # Tag classification + forest_type
  transform/             # Geometry repair, geodesic area, stable ids
  generalise.py          # Zoom-dependent simplify / filter / dissolve
  pipeline.py            # Orchestration, resume, merge/dedupe
  output.py              # GPKG writers + metadata / attribution
```

### Country vs global

| Mode | Units | Notes |
|------|-------|-------|
| `country` | One or more Geofabrik paths for that ISO | Russia uses multiple federal-district extracts |
| `global` | Continental partitions in `countries.yaml` | Avoids loading the planet; units are cached and resumable |

Duplicate geometries at regional boundaries are removed via stable content hashes (`id`).

## Feature mapping (summary)

| Logical feature | OSM sources | Output attrs | Zooms |
|-----------------|-------------|--------------|-------|
| beach | `natural=beach` | `name` | 11+ |
| sand | `natural=sand` | `natural=sand` | 11+ |
| marshland | `natural=wetland`, `wetland=*` | `type`, `area_km2` | 8–12, 12+ |
| marshland_point | centroids of larger wetlands | `type`, `area_km2` | 11+ |
| park | `leisure=park/nature_reserve`, `landuse=recreation_ground`, `boundary=national_park` | `landuse=park` | 11+ |
| orchard | `landuse=orchard` | `landuse`, `name` | 11+ |
| vineyard | `landuse=vineyard` | `landuse`, `name` | 11+ |
| crop | `landuse=farmland/farm/cropland` | `landcover=crop` | 0–9 |
| grass | `landuse=grass/meadow/village_green`, `natural=grassland` | `landcover=grass` | 0–9 |
| woodland | `natural=wood`, `landuse=forest` | `forest_type`, `name` | 8–11, 11–14, 14+ |
| woodland_point | wood/forest **nodes** + large-polygon centroids | `forest_type` | 11+ |

**Not treated as crop:** `orchard` and `vineyard` are dedicated layers; `plant_nursery` remains unmapped.

**Priority (low number wins):** beach → sand → marshland → park → woodland → orchard → vineyard → crop → grass.

**`forest_type`:** `coniferous` / `non_coniferous` from `leaf_type`, `wood:type`, `forest_type`, or species heuristics; else `unknown`.

## Europe: Copernicus Land Monitoring Service (not CLC)

For European extracts, OSM ingest runs **in parallel** with Copernicus Land Monitoring Service [High Resolution Layers](https://land.copernicus.eu/) (Vegetated Land Cover Characteristics, 10 m):

| Outdoors layer | CLMS dataset | Raster values |
|----------------|--------------|---------------|
| woodland | `clms_vlcc_forest-type_europe_10m_3yearly_v1` | 1 broadleaved, 2 coniferous |
| grass | `clms_vlcc_grassland_europe_10m_yearly_v1` | 1 grassland |
| crop | `clms_vlcc_crop-types_europe_10m_yearly_v1` | crop type codes (wheat, maize, …) |

CLMS is used for a zoom profile when **10 m is finer than that profile's OSM simplify tolerance**. In practice: crop/grass (z0–9) and woodland z8–14 come from CLMS; woodland z14+, beach, sand, marsh, park, orchard and vineyard stay OSM.

Live tile download uses the Copernicus Data Space catalogue (`OUTDOORS_CDSE_USERNAME` / `OUTDOORS_CDSE_PASSWORD`). Offline runs skip CLMS unless `--clms-local-root` points at GeoTIFF/COG files. CORINE Land Cover is not downloaded.

**`natural=tree` points are not woodland** — only wood/forest nodes or derived centroids.

## Outputs

Per run under `--output-dir` (default `./outputs/landcover`):

- `{scope}__{layer}__{profile}.gpkg` — GeoPackage
- `{scope}__{layer}__{profile}.geojson` — same layer as GeoJSON (for tippecanoe)
- `{scope}__metadata.json` — provenance, counts, tile layer names, licence
- `{scope}__ATTRIBUTION.txt` — required OSM attribution
- `{scope}__resume.json` — completed unit ids for `--resume`

### Export / MBTiles

```bash
# Convert existing GPKGs → GeoJSON (+ tippecanoe recipe)
outdoors-landcover export \
  --input-dir ./outputs/poland-live \
  --output-dir ./outputs/poland-live/geojson \
  --mbtiles

# If tippecanoe is installed (brew install tippecanoe), MBTiles is built automatically.
# Otherwise run the generated recipe:
./outputs/poland-live/geojson/build_mbtiles.sh
```

## Poland example

See [`examples/poland/README.md`](examples/poland/README.md) for dry-run, offline fixture, and live Geofabrik workflows.

```bash
./examples/poland/run.sh offline
```

## Attribution

OpenStreetMap data is © OpenStreetMap contributors, licensed under the [Open Database Licence (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/). Downstream tiles/styles must retain attribution. Geofabrik redistributes OSM extracts; respect their [download terms](https://download.geofabrik.de/).

## Tests

```bash
pytest
```

All automated tests are offline (fixture-based). No network access required.

## Extending mappings

1. Edit `config/mappings.yaml` (tags, attributes, priority).
2. Adjust zoom behaviour in `config/generalisation.yaml` if needed.
3. Add unit tests in `tests/test_mapping.py` / `tests/test_forest.py`.
4. Re-run `pytest` and a `--dry-run` for a sample country.

## Assumptions & limitations

1. No prior Outdoors tile repo was present — schemas follow this brief; rename via config if needed.
2. Reading live `.osm.pbf` requires GDAL OSM support or pre-converted GeoJSON in cache/`--local-root`.
3. OSM vegetation tagging completeness varies globally; `forest_type=unknown` is common outside well-mapped regions.
4. Global mode uses continental extracts (not every country) to limit duplication and runtime.
5. `area_km2` is emitted for marshland (filtering/styling). Other layers omit it to keep payloads small.
6. France overseas departments are not auto-included with metropolitan France (documented in `countries.yaml`).
7. Boundary clipping between continental extracts relies on feature-id dedupe, not geometric clip — acceptable for landcover polygons that rarely span continents.
8. Ireland uses Geofabrik `europe/ireland-and-northern-ireland` (includes Northern Ireland).
9. Orchard/vineyard are emitted from zoom 11+ (same profile as park); adjust in `generalisation.yaml` if low-zoom coverage is required.
10. Woodland points keep both tagged wood/forest nodes and large-polygon centroids.
