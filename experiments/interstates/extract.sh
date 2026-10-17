#!/bin/sh
# Interstate route relations and their ways from the local US extract.
#   sh experiments/interstates/extract.sh
# Writes cache/rel.opl (relations: refs and member ways) and
# cache/ways.geojsonseq (member way geometry with OSM ids).
set -e
cd "$(dirname "$0")/../.."
c=experiments/interstates/cache
[ -s $c/ways.geojsonseq ] && exit 0
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)":/w -w /w --entrypoint sh iboates/osmium -c "
  osmium tags-filter -O -o /tmp/i.pbf data/us-latest.osm.pbf r/network=US:I &&
  osmium tags-filter -O -R -o /tmp/r.pbf /tmp/i.pbf r/network=US:I &&
  osmium cat -O -f opl -o $c/rel.opl /tmp/r.pbf &&
  osmium export -O -f geojsonseq --geometry-types=linestring -a type,id -o $c/ways.geojsonseq /tmp/i.pbf"
wc -l $c/rel.opl $c/ways.geojsonseq
