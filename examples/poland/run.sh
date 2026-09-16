#!/usr/bin/env bash
# Poland landcover examples: dry-run, offline fixture, or live Geofabrik download.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

MODE="${1:-offline}"
OUT="${OUTDOORS_OUTPUT_DIR:-./outputs/poland}"
CACHE="${OUTDOORS_CACHE_DIR:-./cache/osm}"

case "$MODE" in
  dry-run)
    outdoors-landcover ingest \
      --scope country \
      --country Poland \
      --dry-run \
      --output-dir "$OUT"
    ;;
  offline)
    outdoors-landcover ingest \
      --scope country \
      --country PL \
      --local-root "$ROOT/examples/poland" \
      --offline \
      --output-dir "$OUT" \
      --cache-dir "$CACHE" \
      --temp-dir ./tmp/poland
    ;;
  live)
    echo "'run.sh live' is deprecated — use ./examples/poland/run_live.sh" >&2
    exec "$ROOT/examples/poland/run_live.sh"
    ;;
  *)
    echo "Usage: $0 [dry-run|offline|live]" >&2
    exit 1
    ;;
esac

echo "Done. Outputs under: $OUT"
ls -la "$OUT" | head -40
