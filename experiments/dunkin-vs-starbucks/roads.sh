#!/bin/sh
# Major roads (motorway..tertiary) from the US OSM extract, highway tag only (~8-10 min).
#   sh experiments/dunkin-vs-starbucks/roads.sh
set -e
cd "$(dirname "$0")/../.."
out=experiments/dunkin-vs-starbucks/cache/roads_us.geojsonseq
[ "$(wc -c < "$out" 2>/dev/null || echo 0)" -gt 1000 ] && { echo "cached: $out"; exit 0; }
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)":/w -w /w --entrypoint sh iboates/osmium -c "
  osmium tags-filter -O -o /tmp/r.pbf data/us-latest.osm.pbf w/highway=motorway,trunk,primary,secondary,tertiary \
  && osmium export -O -f geojsonseq -c experiments/dunkin-vs-starbucks/roads_export.json -o $out /tmp/r.pbf"
echo "$(wc -l < "$out") road ways"
