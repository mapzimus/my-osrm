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

echo "== $(date) pack engine"
( cd data/osrm-na-ferry && sudo tar czf "$HOME/engine.tgz" na-ferry.osrm* \
    ferries/ferry-filter-report.json borders/border-report.json )
sudo tar czf "$HOME/reports.tgz" -C data build/*/ferry-report.json 2>/dev/null || true
ls -la "$HOME/engine.tgz"
touch "$HOME/BUILD_DONE"
echo "== $(date) done"
