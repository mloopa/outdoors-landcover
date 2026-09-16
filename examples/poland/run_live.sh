#!/usr/bin/env bash
# Live Geofabrik Poland landcover ingest (no pyosmium required).
#
# Poland has no country-level free shapefile zip on Geofabrik, so this downloads
# all voivodeship *-latest-free.shp.zip extracts, converts landuse/natural layers
# to OSM-tagged GeoJSON, then runs outdoors-landcover.
#
# Usage (from repo root, in your own terminal with network):
#   ./examples/poland/run_live.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

OUT="${OUTDOORS_OUTPUT_DIR:-./outputs/poland-live}"
CACHE="${OUTDOORS_CACHE_DIR:-./cache/osm}"
TMP="${OUTDOORS_TEMP_DIR:-./tmp/poland-live}"
SHP_DIR="$CACHE/poland_shp"
GEOJSON="$CACHE/europe_poland.geojson"
BASE="https://download.geofabrik.de/europe"

# Geofabrik sub-regions of Poland that publish free shapefiles
VOIVODESHIPS=(
  dolnoslaskie
  kujawsko-pomorskie
  lodzkie
  lubelskie
  lubuskie
  malopolskie
  mazowieckie
  opolskie
  podkarpackie
  podlaskie
  pomorskie
  slaskie
  swietokrzyskie
  warminsko-mazurskie
  wielkopolskie
  zachodniopomorskie
)

mkdir -p "$CACHE" "$OUT" "$TMP" "$SHP_DIR"

if [[ ! -f .venv/bin/activate ]]; then
  echo "Create a venv first: python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'" >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Downloading Geofabrik voivodeship shapefiles (landuse + natural)"
echo "    Note: poland-latest-free.shp.zip is NOT published for whole Poland;"
echo "    pyosmium also has no wheel for Apple CLT Python 3.9 / macOS 26 —"
echo "    shapefiles avoid both problems."

UA="outdoors-landcover/0.1 (+https://www.openstreetmap.org/copyright)"
for region in "${VOIVODESHIPS[@]}"; do
  dest="$SHP_DIR/${region}-latest-free.shp.zip"
  if [[ -f "$dest" && -s "$dest" ]]; then
    echo "  cached: $region"
    continue
  fi
  url="$BASE/poland/${region}-latest-free.shp.zip"
  echo "  downloading: $url"
  curl -L --fail --retry 3 -A "$UA" -o "${dest}.partial" "$url"
  mv "${dest}.partial" "$dest"
done

echo "==> Converting shapefiles → $GEOJSON"
.venv/bin/python3 scripts/geofabrik_shp_to_geojson.py \
  --shp-dir "$SHP_DIR" \
  --output "$GEOJSON"

echo "==> Running outdoors-landcover ingest for PL"
outdoors-landcover ingest \
  --scope country \
  --country PL \
  --local-root "$CACHE" \
  --offline \
  --output-dir "$OUT" \
  --cache-dir "$CACHE" \
  --temp-dir "$TMP" \
  --concurrency 1

echo "==> Done. Outputs in $OUT"
ls -lh "$OUT" | head -40
echo
echo "Open a few layers in QGIS, e.g.:"
echo "  $OUT/PL__woodland__z14_plus.gpkg"
echo "  $OUT/PL__crop__z0_9.gpkg"
