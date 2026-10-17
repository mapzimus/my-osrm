#!/bin/sh
# Theme parks and their rides from the local OSM extracts (Overpass is often busy).
#   sh experiments/amusement_parks/extract.sh
set -e
cd "$(dirname "$0")/../.."
for r in mexico puerto-rico canada us; do
  out=experiments/amusement_parks/cache/osm_$r.geojson
  [ -s "$out" ] && continue
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)":/w -w /w --entrypoint sh iboates/osmium -c "
    osmium tags-filter -O -o /tmp/f.pbf data/$r-latest.osm.pbf \
      nwr/tourism=theme_park nwr/leisure=water_park \
      nwr/attraction=roller_coaster,amusement_ride,carousel,big_wheel,water_slide,train \
      nwr/roller_coaster && osmium export -O -o $out /tmp/f.pbf"
  echo "$r: $(grep -c '"type":"Feature"' "$out" || true) features"
done
