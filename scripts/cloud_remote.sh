#!/usr/bin/env bash
# cloud_remote.sh - runs ON the build VM. Same pipeline as BRONTOSAURUS:
# downloads the extracts from Geofabrik (fast from inside GCP), builds the
# tools image, runs update_data.py + build_ferry_engine.py, tars the engine.
#
# Driven by scripts/cloud_build.ps1, which sets REGION (na | eu); writes
# ~/build.log and ~/BUILD_DONE.
set -euo pipefail
cd "$HOME/my-osrm"

REGION="${REGION:-na}"
case "$REGION" in
    na) NAME=na-ferry; COUNTRIES="us ca mx cam pr bm gl"; THREADS=8 ;;
    eu) NAME=eu-ferry; COUNTRIES="eu";                    THREADS=16 ;;
    *)  echo "unknown REGION $REGION"; exit 2 ;;
esac
OUT="data/osrm-$NAME"

echo "== $(date) packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 python3 curl >/dev/null

echo "== $(date) tools image"
sudo docker build -q -t my-osrm-tools tools/

mkdir -p data
echo "== $(date) extracts ($REGION: $COUNTRIES)"
# shellcheck disable=SC2086
sudo python3 scripts/update_data.py --region "$REGION" $COUNTRIES --no-build

echo "== $(date) build ($REGION, $THREADS threads)"
sudo OSRM_THREADS=$THREADS python3 scripts/build_ferry_engine.py --region "$REGION"

echo "== $(date) verify on the VM (before anything is fetched)"
# Serve the engine here and run the report card + ferry sweep, so a bad build
# is caught while every intermediate file still exists to debug it.
sudo docker rm -f verify-osrm >/dev/null 2>&1 || true
# tr -d '\r': the bundle is tarred on Windows, so docker-compose.yml has CRLF
# endings and the image name kept a trailing \r ("invalid reference format").
OSRM_IMAGE="$(grep -m1 'image: ghcr.io/project-osrm' docker-compose.yml | awk '{print $2}' | tr -d '\r')"
sudo docker run -d --name verify-osrm -p 5002:5000 -v "$PWD/$OUT:/data:ro" \
    "$OSRM_IMAGE" osrm-routed --algorithm mld --max-table-size 8000 "/data/$NAME.osrm"
# A point inside the engine's area: any snap answers once the engine is up.
case "$REGION" in na) PROBE="-77.0,38.9;-77.0,38.91" ;; eu) PROBE="13.40,52.52;13.41,52.52" ;; esac
for i in $(seq 1 90); do curl -sf "http://127.0.0.1:5002/route/v1/driving/$PROBE" >/dev/null && break; sleep 10; done
python3 benchmarks/report_card.py --url http://127.0.0.1:5002 --engine "$REGION" | tee "$HOME/report_card.txt" || true
# The ferry sweep reads the NA report paths; Europe gets the report card only.
[ "$REGION" = na ] && { python3 benchmarks/ferry_sweep.py --url http://127.0.0.1:5002 || true; }
sudo docker rm -f verify-osrm >/dev/null 2>&1 || true

echo "== $(date) pack engine"
( cd "$OUT" && sudo tar czf "$HOME/engine.tgz" "$NAME".osrm*     ferries/ferry-filter-report.json ferries/ferry-durations-report.json borders/border-report.json )
# Reports: paths relative to the repo root (the old "-C data build/*/..."
# glob expanded in the wrong directory and produced an empty archive).
sudo tar czf "$HOME/reports.tgz" data/build/*/ferry-report.json "$HOME/report_card.txt" \
    $( [ "$REGION" = na ] && echo benchmarks/ferry_sweep.md benchmarks/ferry_sweep.json )
# The final network, for debugging without another build (~0.8 GB for NA).
sudo cp "$OUT/$NAME.osm.pbf" "$HOME/$NAME.osm.pbf"
ls -la "$HOME/engine.tgz" "$HOME/reports.tgz" "$HOME/$NAME.osm.pbf"
touch "$HOME/BUILD_DONE"
echo "== $(date) done"
