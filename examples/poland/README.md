# Poland example

End-to-end Outdoors landcover ingestion for Poland (`PL` / `Poland`).

Geofabrik extract: `europe/poland` → `https://download.geofabrik.de/europe/poland-latest.osm.pbf`

## Quick start (offline, no download)

Uses the checked-in synthetic fixture `europe_poland.geojson` (Polish place names, illustrative geometries).

```bash
cd outdoors
source .venv/bin/activate   # after pip install -e ".[dev]"

# Via helper script
chmod +x examples/poland/run.sh
./examples/poland/run.sh offline

# Or directly
outdoors-landcover ingest \
  --scope country \
  --country PL \
  --local-root ./examples/poland \
  --offline \
  --output-dir ./outputs/poland
```

Expected GeoPackages under `./outputs/poland/`:

| File | Tile layer name |
|------|-----------------|
| `PL__beach__z11_plus.gpkg` | `beach` |
| `PL__sand__z11_plus.gpkg` | `sand` |
| `PL__marshland__z8_12.gpkg` / `…z12_plus` | `marshland` |
| `PL__marshland_point__z11_plus.gpkg` | `marshland_point` |
| `PL__park__z11_plus.gpkg` | `park` |
| `PL__orchard__z11_plus.gpkg` | `orchard` |
| `PL__vineyard__z11_plus.gpkg` | `vineyard` |
| `PL__crop__z0_9.gpkg` | `crop` |
| `PL__grass__z0_9.gpkg` | `grass` |
| `PL__woodland__z8_9.gpkg` / `…z10` / `…z11_14` / `…z14_plus` | `woodland` |
| `PL__woodland_point__z11_plus.gpkg` | `woodland_point` |

Also written: `PL__metadata.json`, `PL__ATTRIBUTION.txt`, `PL__resume.json`.

## Dry-run (plan only)

```bash
./examples/poland/run.sh dry-run
# or
outdoors-landcover ingest --scope country --country Poland --dry-run \
  --output-dir ./outputs/poland
```

## Live Geofabrik import

`pyosmium` has **no pip wheel** for Apple CLT Python 3.9 on macOS 26 (that was the
error you hit). Poland also has **no** country-level `*-free.shp.zip` on Geofabrik.

`./examples/poland/run_live.sh` therefore downloads all **16 voivodeship** shapefile
zips, converts `landuse`/`natural` layers to OSM-tagged GeoJSON (via geopandas),
and runs ingest — no pyosmium required.

```bash
cd /Users/michallupa/Documents/Projekty/outdoors
source .venv/bin/activate
./examples/poland/run_live.sh
```

First run downloads several GB into `cache/osm/poland_shp/` (cached afterwards).
Outputs go to `./outputs/poland-live/`.

**Limitation vs raw PBF:** shapefiles omit `leaf_type`/species, so woodland is mostly
`forest_type=unknown`. Area coverage for forest/crop/grass/park/orchard/vineyard/
beach/sand/wetland is still nationwide.

## Fixture contents

The offline fixture includes beach (Sopot), sand, Biebrza marsh, Łazienki park, farmland, meadow, coniferous/non-coniferous/unknown woodland, a wood node, Grójec orchard, Srebrna Góra vineyard, and Tatra national park — enough to exercise every Outdoors landcover class.
