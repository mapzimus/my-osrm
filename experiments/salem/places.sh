#!/bin/sh
# Every named place node (city/town/village/hamlet) in the US extract, name tags only (~5 min).
#   sh experiments/salem/places.sh
set -e
cd "$(dirname "$0")/../.."
out=experiments/salem/cache/places_us.geojsonseq
[ -s "$out" ] && { echo "cached: $out"; exit 0; }
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)":/w -w /w --entrypoint sh iboates/osmium -c "
  osmium tags-filter -O -o /tmp/p.pbf data/us-latest.osm.pbf n/place=city,town,village,hamlet \
  && osmium export -O -f geojsonseq -o $out /tmp/p.pbf"
echo "$(wc -l < "$out") places"
