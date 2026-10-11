"""Paths, engines and version pins. Everything the app needs to know that
isn't derived from the code.

Engines mirror docker-compose.yml. `scope` is what benchmarks/report_card.py
calls `--engine`: "na" engines are graded on Canada cases, "us" ones skip them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
APP_DATA = DATA / "app"            # derived: places.sqlite, ferries.geojson
BASEMAP_DIR = DATA / "basemap"     # *.pmtiles
BIN = DATA / "bin"                 # pmtiles.exe
WEB = ROOT / "app" / "web"
VENDOR = WEB / "vendor"
ISOCHRONE_DIR = ROOT / "experiments" / "isochrone"
BENCHMARKS_DIR = ROOT / "benchmarks"

HOST = "127.0.0.1"
PORT = 8080


@dataclass(frozen=True)
class Engine:
    id: str
    title: str
    url: str
    compose_service: str
    scope: str                      # "na" | "us"
    color: str                      # line color in the compare tool


ENGINES: dict[str, Engine] = {
    e.id: e for e in (
        Engine("na-ferry", "US + Canada + Mexico + ferries", "http://127.0.0.1:5002", "na-ferry", "na", "#d1495b"),
        Engine("na-major", "US + Canada major roads", "http://127.0.0.1:5001", "na-major", "na", "#1f77b4"),
        Engine("us-major", "US major roads", "http://127.0.0.1:5000", "us-major", "us", "#2ca02c"),
    )
}
DEFAULT_ENGINE = "na-ferry"

# Vendored front-end libraries (npm registry tarballs, see app/setup/vendor.py)
NPM_PINS = {
    "maplibre-gl": "6.13.0",
    "pmtiles": "4.5.0",
    "fflate": "0.8.2",
    "@protomaps/basemaps": "5.7.2",
}
GO_PMTILES_VERSION = "1.31.2"

# Protomaps daily basemap build. Builds older than a week disappear, so
# setup picks the newest one it can see and records it in data/basemap/BUILD.txt.
# Set this to pin a specific build id (YYYYMMDD) instead.
BASEMAP_BUILD: str | None = None
BASEMAP_BBOX = (-170.0, 5.0, -50.0, 72.0)      # North America incl. Alaska, Mexico, Central America
BASEMAP_FILE = "north-america.pmtiles"

TOOLS_IMAGE = "my-osrm-tools"                  # tools/Dockerfile: osmium + pyosmium + shapely
