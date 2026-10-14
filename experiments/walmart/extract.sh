#!/bin/sh
# Every Walmart-branded feature from the local OSM extracts.
#   sh experiments/walmart/extract.sh
set -e
cd "$(dirname "$0")/../.."
mkdir -p experiments/walmart/cache
for r in us puerto-rico; do
  out=experiments/walmart/cache/osm_$r.geojson
  [ -s "$out" ] && continue
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)":/w -w /w --entrypoint sh iboates/osmium -c "
    osmium tags-filter -O -o /tmp/f.pbf data/$r-latest.osm.pbf \
      nwr/brand:wikidata=Q483551 nwr/brand=Walmart && osmium export -O -o $out /tmp/f.pbf"
  echo "$r: $(grep -c '"type":"Feature"' "$out" || true) features"
done
