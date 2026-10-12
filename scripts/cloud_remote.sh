#!/usr/bin/env bash
# cloud_remote.sh - runs ON the build VM. Same pipeline as BRONTOSAURUS:
# downloads the extracts from Geofabrik (fast from inside GCP), builds the
# tools image, runs update_data.py + build_ferry_engine.py, tars the engine.
#
# Driven by scripts/cloud_build.ps1; writes ~/build.log and ~/BUILD_DONE.
set -euo pipefail
cd "$HOME/my-osrm"

echo "== $(date) packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 python3 curl >/dev/null

echo "== $(date) tools image"
sudo docker build -q -t my-osrm-tools tools/

mkdir -p data
echo "== $(date) extracts"
sudo python3 scripts/update_data.py us ca mx cam pr bm gl --no-build

echo "== $(date) build (8 threads, 64 GB)"
sudo OSRM_THREADS=8 python3 scripts/build_ferry_engine.py

echo "== $(date) verify on the VM (before anything is fetched)"
# Serve the engine here and run the report card + ferry sweep, so a bad build
# is caught while every intermediate file still exists to debug it.
sudo docker rm -f verify-osrm >/dev/null 2>&1 || true
sudo docker run -d --name verify-osrm -p 5002:5000 -v "$PWD/data/osrm-na-ferry:/data:ro"     "$(grep -m1 'image: ghcr.io/project-osrm' docker-compose.yml | awk '{print $2}')"     osrm-routed --algorithm mld --max-table-size 8000 /data/na-ferry.osrm
for i in $(seq 1 60); do curl -sf "http://127.0.0.1:5002/route/v1/driving/-77.0,38.9;-77.0,38.91" >/dev/null && break; sleep 10; done
python3 benchmarks/report_card.py --url http://127.0.0.1:5002 --engine na | tee "$HOME/report_card.txt" || true
python3 benchmarks/ferry_sweep.py --url http://127.0.0.1:5002 || true
sudo docker rm -f verify-osrm >/dev/null 2>&1 || true

echo "== $(date) pack engine"
( cd data/osrm-na-ferry && sudo tar czf "$HOME/engine.tgz" na-ferry.osrm*     ferries/ferry-filter-report.json ferries/ferry-durations-report.json borders/border-report.json )
# Reports: paths relative to the repo root (the old "-C data build/*/..."
# glob expanded in the wrong directory and produced an empty archive).
sudo tar czf "$HOME/reports.tgz" data/build/*/ferry-report.json     benchmarks/ferry_sweep.md benchmarks/ferry_sweep.json "$HOME/report_card.txt"
# The final network, for debugging without another build (~0.8 GB).
sudo cp data/osrm-na-ferry/na-ferry.osm.pbf "$HOME/na-ferry.osm.pbf"
ls -la "$HOME/engine.tgz" "$HOME/reports.tgz" "$HOME/na-ferry.osm.pbf"
touch "$HOME/BUILD_DONE"
echo "== $(date) done"
