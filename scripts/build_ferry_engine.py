"""Build the North America major-roads engine WITH ferries (US + Canada + Mexico).

Per country, from its raw Geofabrik extract in data/:
    1. major-roads filter   -> the country's *-major.osm.pbf
    2. ferry_connectors.py  -> data/build/<cc>/ferry-connectors.osm.pbf
    3. (US, Canada) unclassified roads north of NORTH_LAT
Then merge, dedup (newest version of each object), mark national border
crossings (border_crossings.py), and run extract -> partition -> customize
with profiles/car.lua.

Output: data/osrm-na-ferry/na-ferry.osrm*  (served on :5002 by docker compose)

Runs from Windows, WSL, or Linux: it only shells out to docker.
Idempotent: each stage is skipped if its output already exists. To add a
country, add a row to COUNTRIES and drop its extract in data/.

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

# code: (raw extract, major-roads PBF), paths relative to data/
COUNTRIES = {
    "us": ("us-latest.osm.pbf", "osrm-major/us-major.osm.pbf"),
    "ca": ("canada-latest.osm.pbf", "osrm-major-na/canada-major.osm.pbf"),
    "mx": ("mexico-latest.osm.pbf", "build/mx/mexico-major.osm.pbf"),
    "cam": ("central-america-latest.osm.pbf", "build/cam/central-america-major.osm.pbf"),
    # Not part of Geofabrik's US extract; island network (Vieques/Culebra car ferries).
    "pr": ("puerto-rico-latest.osm.pbf", "build/pr/puerto-rico-major.osm.pbf"),
}
MAJOR_FILTER = (
    "w/highway=motorway,trunk,primary,secondary,tertiary,"
    "motorway_link,trunk_link,primary_link,secondary_link,tertiary_link"
)
# The far north also keeps its lowest public roads. "unclassified" means road
# class, not pavement (the gravel Dempster Hwy is trunk). Dropping them
# everywhere is only a memory measure for the dense US grid; up north they're
# sparse (~31k segments in Canada) and often the only road. Tracks stay out:
# car.lua has no speed for them, so OSRM would never drive them anyway.
NORTH_LAT = 55
NORTH_COUNTRIES = ("us", "ca")                     # Alaska + northern Canada
NORTH_FILTER = "w/highway=unclassified,road"


def docker(image: str, *args: str) -> None:
    # Our car.lua replaces the stock one in place, so its require('lib/...')
    # calls still resolve against the image's /opt/lib.
    cmd = ["docker", "run", "--rm", "-v", f"{DATA}:/data",
           "-v", f"{ROOT / 'profiles' / 'car.lua'}:/opt/car.lua:ro",
           "-v", f"{ROOT / 'scripts'}:/scripts:ro", image, *args]
    print("+", " ".join(cmd[9:]), flush=True)
    subprocess.run(cmd, check=True)


def stage(sentinel: Path, label: str, image: str, *args: str) -> None:
    # A stage counts as done only once its .done marker exists, written after
    # the command succeeds. An interrupted stage can leave a truncated output
    # behind; without the marker it re-runs instead of being trusted.
    done = sentinel.with_name(sentinel.name + ".done")
    if sentinel.exists() and done.exists():
        print(f"[skip] {label}: {sentinel.name} done")
        return
    print(f"[run]  {label}")
    docker(image, *args)
    done.touch()


def main() -> None:
    inputs = []
    for cc, (raw, major) in COUNTRIES.items():
        if not (DATA / raw).exists():
            raise SystemExit(f"missing extract for {cc}: data/{raw}")
        (DATA / major).parent.mkdir(parents=True, exist_ok=True)
        stage(DATA / major, f"{cc}: major-roads filter", TOOLS_IMAGE,
              "osmium", "tags-filter", "--overwrite", "-o", f"/data/{major}", f"/data/{raw}", MAJOR_FILTER)
        connectors = f"build/{cc}/ferry-connectors.osm.pbf"
        stage(DATA / connectors, f"{cc}: ferry connectors", TOOLS_IMAGE,
              "python", "/scripts/ferry_connectors.py", f"/data/{raw}", f"/data/build/{cc}")
        inputs += [major, connectors]
        if cc in NORTH_COUNTRIES:
            north = f"build/{cc}/north-{NORTH_LAT}.osm.pbf"
            stage(DATA / north, f"{cc}: north of {NORTH_LAT}N", TOOLS_IMAGE,
                  "osmium", "extract", "--overwrite", "-b", f"-180,{NORTH_LAT},-50,85",
                  "-o", f"/data/{north}", f"/data/{raw}")
            north_minor = f"build/{cc}/north-{NORTH_LAT}-minor.osm.pbf"
            stage(DATA / north_minor, f"{cc}: north minor roads", TOOLS_IMAGE,
                  "osmium", "tags-filter", "--overwrite", "-o", f"/data/{north_minor}",
                  f"/data/{north}", NORTH_FILTER)
            inputs.append(north_minor)

    OUT.mkdir(exist_ok=True)
    out = "/data/osrm-na-ferry"
    merged, dedup = OUT / "na-merged.osm.pbf", OUT / "na-dedup.osm.pbf"
    pbf = OUT / f"{NAME}.osm.pbf"
    osrm = OUT / f"{NAME}.osrm"
    stage(merged, "merge", TOOLS_IMAGE, "osmium", "merge", "--overwrite",
          "-o", f"{out}/{merged.name}", *[f"/data/{p}" for p in inputs])
    # Extracts from different days carry different versions of the same
    # border-area objects; merge keeps both (582 duplicate nodes in Oct 2026).
    # time-filter with no time keeps the newest version of each.
    stage(dedup, "dedup", TOOLS_IMAGE, "osmium", "time-filter", "--overwrite",
          "-o", f"{out}/{dedup.name}", f"{out}/{merged.name}")
    stage(pbf, "border crossings", TOOLS_IMAGE, "python", "/scripts/border_crossings.py",
          f"{out}/{dedup.name}", f"{out}/{pbf.name}", f"{out}/borders",
          *[f"/data/{raw}" for raw, _ in COUNTRIES.values()])
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
