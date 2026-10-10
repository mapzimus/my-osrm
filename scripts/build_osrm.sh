#!/usr/bin/env bash
# build_osrm.sh — build a single-extract OSRM engine (US-only by default).
#
# Downloads an OSM extract (default: Geofabrik US) and runs the three-stage
# osrm-backend pipeline against it with profiles/car.lua. Result: a
# ready-to-serve <base>.osrm under the output dir.
#
# For the recommended US+Canada+ferries engine, use
# scripts/build_ferry_engine.py instead.
#
# Requirements: docker, ~25GB free disk, ~30-60min on first run (extract +
# partition + customize is CPU-bound).
#
# Usage:
#   ./scripts/build_osrm.sh                                   # full US (cloud VM only)
#   ./scripts/build_osrm.sh data/osrm-major/us-major.osm.pbf data/osrm-major us-major
#
# Idempotent — checks for each artifact before recomputing.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
# Pinned: OSRM v26.5.0, the image that built the existing artifacts.
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend@sha256:aa6a1de3a71dafffd0ba39340542524f66e6841fc19bf7874a0e6a7967837f56"
PROFILE="${REPO_ROOT}/profiles/car.lua"

# Positional args (all optional, default to original full-US behavior):
#   $1 = PBF source URL or existing local PBF path (default: Geofabrik US)
#   $2 = output dir relative to repo root (default: data/osrm)
#   $3 = OSRM base filename in that dir (default: us-latest)
PBF_SRC="${1:-https://download.geofabrik.de/north-america/us-latest.osm.pbf}"
OSRM_DIR="${REPO_ROOT}/${2:-data/osrm}"
OSRM_BASE="${3:-us-latest}"

PBF_FILE="${OSRM_DIR}/${OSRM_BASE}.osm.pbf"
OSRM_FILE="${OSRM_DIR}/${OSRM_BASE}.osrm"

# osrm-extract auto-detects host CPU count and spins one worker thread per
# core. On a 16-core box that pushes peak RSS during "parse ways and nodes"
# above 25 GB for the full US extract and can OOM-kill the container on
# memory-tight machines. Cap threads here. 6 is a balanced default for
# 16-32 GB hosts; bump up if you have headroom.
THREADS="${OSRM_THREADS:-6}"

mkdir -p "${OSRM_DIR}"

log() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }

# --- 1. Ensure the source PBF exists at PBF_FILE ---
# Three cases:
#   - PBF_FILE already exists at destination: nothing to do
#   - PBF_SRC is a local file that already lives at PBF_FILE: nothing to do
#     (this is the filtered-PBF case — file is already in data/osrm-major/)
#   - PBF_SRC is a local file elsewhere: copy it into PBF_FILE
#   - PBF_SRC is a URL: download to PBF_FILE
mkdir -p "${OSRM_DIR}"
if [[ -f "${PBF_FILE}" ]]; then
    log "PBF already present: ${PBF_FILE} ($(du -h "${PBF_FILE}" | cut -f1))"
elif [[ -f "${PBF_SRC}" ]]; then
    if [[ "$(realpath "${PBF_SRC}")" == "$(realpath -m "${PBF_FILE}")" ]]; then
        log "PBF already at destination: ${PBF_FILE}"
    else
        log "Copying PBF: ${PBF_SRC} -> ${PBF_FILE}"
        cp "${PBF_SRC}" "${PBF_FILE}"
    fi
else
    log "Downloading ${PBF_SRC} (this is ~10 GB — go get coffee)"
    curl -L -C - -o "${PBF_FILE}" "${PBF_SRC}"
fi

# --- 2. osrm-extract ---
# Sentinel is .fileIndex: OSRM v26 never writes a bare <base>.osrm file, so
# checking for it (as this script used to) re-ran the 30+ min extract every time.
if [[ -f "${OSRM_FILE}.fileIndex" ]]; then
    log "Extract artifact already present: ${OSRM_FILE}.fileIndex"
else
    log "Running osrm-extract (profiles/car.lua, threads=${THREADS})"
    docker run --rm -t \
        -v "${OSRM_DIR}:/data" \
        -v "${PROFILE}:/opt/car.lua:ro" \
        "${OSRM_IMAGE}" \
        osrm-extract -p /opt/car.lua --threads "${THREADS}" "/data/${OSRM_BASE}.osm.pbf"
fi

# --- 3. osrm-partition ---
# Partition produces several files; check for .osrm.partition as the sentinel.
if [[ -f "${OSRM_FILE}.partition" ]]; then
    log "Partition artifact already present"
else
    log "Running osrm-partition (this is the slow step — ~30 min, threads=${THREADS})"
    docker run --rm -t \
        -v "${OSRM_DIR}:/data" \
        "${OSRM_IMAGE}" \
        osrm-partition --threads "${THREADS}" "/data/${OSRM_BASE}.osrm"
fi

# --- 4. osrm-customize ---
# Sentinel is .cell_metrics (produced by osrm-customize). The old script used
# .cells which is actually produced by osrm-partition, so customize was
# silently skipped on the filtered build of 2026-05-21. Caught at execution.
if [[ -f "${OSRM_FILE}.cell_metrics" ]]; then
    log "Customize artifact already present"
else
    log "Running osrm-customize (threads=${THREADS})"
    docker run --rm -t \
        -v "${OSRM_DIR}:/data" \
        "${OSRM_IMAGE}" \
        osrm-customize --threads "${THREADS}" "/data/${OSRM_BASE}.osrm"
fi

log "OSRM artifacts ready in ${OSRM_DIR}"
log "Serve it via a docker-compose.yml service, then score it:"
log "  python benchmarks/report_card.py --url http://127.0.0.1:<port>"
