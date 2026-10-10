"""Build the US+Canada major-roads engine WITH ferries.

Inputs (already on disk):
    data/osrm-major/us-major.osm.pbf              US major roads
    data/osrm-major-na/canada-major.osm.pbf       Canada major roads
    data/build/{us,ca}/ferry-connectors.osm.pbf   from ferry_connectors.py

Output: data/osrm-na-ferry/na-ferry.osrm*  (serve on :5002 to A/B against :5001)

Runs from Windows, WSL, or Linux: it only shells out to docker.
Idempotent: each stage is skipped if its output already exists.

    python scripts/build_ferry_engine.py
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "osrm-na-ferry"
NAME = "na-ferry"
# Pinned: the exact image that built the May 2026 artifacts (OSRM v26.5.0).
OSRM_IMAGE = (
    "ghcr.io/project-osrm/osrm-backend@sha256:"
    "aa6a1de3a71dafffd0ba39340542524f66e6841fc19bf7874a0e6a7967837f56"
)
TOOLS_IMAGE = "my-osrm-tools"
THREADS = os.environ.get("OSRM_THREADS", "6")

INPUTS = [
    "osrm-major/us-major.osm.pbf",
    "osrm-major-na/canada-major.osm.pbf",
    "build/us/ferry-connectors.osm.pbf",
    "build/ca/ferry-connectors.osm.pbf",
]


def docker(image: str, *args: str) -> None:
    # Our car.lua replaces the stock one in place, so its require('lib/...')
    # calls still resolve against the image's /opt/lib.
    cmd = ["docker", "run", "--rm", "-v", f"{DATA}:/data",
           "-v", f"{ROOT / 'profiles' / 'car.lua'}:/opt/car.lua:ro", image, *args]
    print("+", " ".join(cmd[7:]), flush=True)
    subprocess.run(cmd, check=True)


def stage(sentinel: Path, label: str, image: str, *args: str) -> None:
    if sentinel.exists():
        print(f"[skip] {label}: {sentinel.name} exists")
        return
    print(f"[run]  {label}")
    docker(image, *args)


def main() -> None:
    missing = [p for p in INPUTS if not (DATA / p).exists()]
    if missing:
        raise SystemExit(f"missing inputs: {missing}")
    OUT.mkdir(exist_ok=True)
    pbf = OUT / f"{NAME}.osm.pbf"
    osrm = OUT / f"{NAME}.osrm"

    stage(pbf, "merge", TOOLS_IMAGE, "osmium", "merge", "--overwrite",
          "-o", f"/data/osrm-na-ferry/{pbf.name}", *[f"/data/{p}" for p in INPUTS])
    stage(osrm.with_suffix(".osrm.fileIndex"), "extract", OSRM_IMAGE,
          "osrm-extract", "-p", "/opt/car.lua", "--threads", THREADS,
          f"/data/osrm-na-ferry/{pbf.name}")
    stage(osrm.with_suffix(".osrm.partition"), "partition", OSRM_IMAGE,
          "osrm-partition", "--threads", THREADS, f"/data/osrm-na-ferry/{osrm.name}")
    stage(osrm.with_suffix(".osrm.cell_metrics"), "customize", OSRM_IMAGE,
          "osrm-customize", "--threads", THREADS, f"/data/osrm-na-ferry/{osrm.name}")
    print(f"\nready: {osrm}")


if __name__ == "__main__":
    main()
