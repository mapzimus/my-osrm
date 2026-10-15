#!/bin/sh
# Through roads (motorway..unclassified) with name and ref, for the one-road crossing count (~10 min).
#   sh experiments/border_crossings/roads.sh
set -e
cd "$(dirname "$0")/../.."
out=experiments/border_crossings/cache/roads_named.geojsonseq
[ "$(wc -c < "$out" 2>/dev/null || echo 0)" -gt 1000 ] && { echo "cached: $out"; exit 0; }
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)":/w -w /w --entrypoint sh iboates/osmium -c "
  osmium tags-filter -O -o /tmp/r.pbf data/us-latest.osm.pbf w/highway=motorway,trunk,primary,secondary,tertiary,unclassified \
  && osmium export -O -f geojsonseq -c experiments/border_crossings/roads_export.json -o $out /tmp/r.pbf"
echo "$(wc -l < "$out") features"
