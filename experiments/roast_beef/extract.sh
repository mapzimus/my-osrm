#!/bin/sh
# Every named food place in Massachusetts from the local US OSM extract (~6-8 min);
# inventory.py picks the roast beef ones by name.
#   sh experiments/roast_beef/extract.sh
set -e
cd "$(dirname "$0")/../.."
out=experiments/roast_beef/cache/osm_ma_food.geojson
[ -s "$out" ] && { echo "cached: $out"; exit 0; }
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)":/w -w /w --entrypoint sh iboates/osmium -c "
  osmium extract -O -b -73.51,41.23,-69.92,42.89 -o /tmp/ma.pbf data/us-latest.osm.pbf \
  && osmium tags-filter -O -o /tmp/f.pbf /tmp/ma.pbf nwr/amenity=restaurant,fast_food,cafe,food_court nwr/shop=deli,butcher,convenience \
  && osmium export -O -o $out /tmp/f.pbf"
echo "$(grep -c '"type":"Feature"' "$out" || true) features"
