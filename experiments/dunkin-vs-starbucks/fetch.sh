#!/bin/sh
# Store locations from AllThePlaces (CC-0; scraped from each chain's own store
# locator, rebuilt weekly). OSM had only ~70% of stores (Max picked ATP, 2026-10-14).
#   sh experiments/dunkin-vs-starbucks/fetch.sh            # latest run
#   RUN=2026-10-10-13-32-41 sh experiments/dunkin-vs-starbucks/fetch.sh
set -e
cd "$(dirname "$0")/cache"
mkdir -p atp
RUN=${RUN:-$(curl -sL https://data.alltheplaces.xyz/runs/latest.json | sed 's/.*"run_id":"\([^"]*\)".*/\1/')}
for s in dunkin_us starbucks_us; do
  [ -s "atp/$s.geojson" ] && continue
  curl -sfL -o "atp/$s.geojson" "https://alltheplaces-data.openaddresses.io/runs/$RUN/output/$s.geojson"
done
echo "$RUN" > atp/run.txt
echo "AllThePlaces run $RUN"
