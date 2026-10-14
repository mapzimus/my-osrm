#!/bin/sh
# Every Dunkin' and Starbucks feature from the local US OSM extract (~5-8 min).
#   sh experiments/dunkin-vs-starbucks/extract.sh
set -e
cd "$(dirname "$0")/../.."
out=experiments/dunkin-vs-starbucks/cache/osm_us.geojson
[ -s "$out" ] && { echo "cached: $out"; exit 0; }
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)":/w -w /w --entrypoint sh iboates/osmium -c "
  osmium tags-filter -O -o /tmp/f.pbf data/us-latest.osm.pbf -e experiments/dunkin-vs-starbucks/filters.txt \
  && osmium export -O -o $out /tmp/f.pbf"
echo "$(grep -c '"type":"Feature"' "$out" || true) features"
