"""Build the North America major-roads engine WITH ferries (US + Canada + Mexico).

Per country, from its raw Geofabrik extract in data/:
    1. major-roads filter   -> the country's *-major.osm.pbf
    2. ferry_connectors.py  -> data/build/<cc>/ferry-connectors.osm.pbf
    3. (US, Canada) unclassified roads north of NORTH_LAT
Then merge, dedup (newest version of each object), mark national border
crossings (border_crossings.py), drop ferries that don't carry cars
(car_ferries.py), and run extract -> partition -> customize with
profiles/car.lua.

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
# MY_OSRM_DATA lets benchmarks/mini_engine.py run this exact pipeline on a
# folder of small cut-outs (same file names) instead of the real data.
DATA = Path(os.environ.get("MY_OSRM_DATA", ROOT / "data")).resolve()
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
    # openstreetmap.fr extract (Geofabrik has none).
    "bm": ("bermuda-latest.osm.pbf", "build/bm/bermuda-major.osm.pbf"),
    # No road between towns; each is reached by plane (flights.py).
    "gl": ("greenland-latest.osm.pbf", "build/gl/greenland-major.osm.pbf"),
}
MAJOR_FILTER = (
    "w/highway=motorway,trunk,primary,secondary,tertiary,"
    "motorway_link,trunk_link,primary_link,secondary_link,tertiary_link"
)
# Turn restrictions are RELATIONS; a ways-only filter drops every one of them
# (the Oct 2026 engine had 0 relations: no no-left-turn anywhere). osmium
# keeps the relation plus its member ways and nodes.
RESTRICTIONS = "r/type=restriction"
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


def stage(sentinel: Path, label: str, image: str, *args: str,
          inputs: tuple[Path, ...] = ()) -> None:
    # A stage counts as done only once its .done marker exists (written after
    # the command succeeds) AND the marker is newer than every input. Without
    # the freshness test, adding a country or refreshing an extract silently
    # served the old network; without the marker, an interrupted stage's
    # truncated output was trusted.
    done = sentinel.with_name(sentinel.name + ".done")
    # Size > 0 too: a crashed run left 0-byte outputs that looked complete.
    if sentinel.exists() and sentinel.stat().st_size > 0 and done.exists():
        stale = [p for p in inputs if p.exists() and p.stat().st_mtime > done.stat().st_mtime]
        if not stale:
            print(f"[skip] {label}: {sentinel.name} done")
            return
        print(f"[redo] {label}: newer input {stale[0].name}")
    else:
        print(f"[run]  {label}")
    docker(image, *args)
    done.touch()


def main() -> None:
    # Memory: a running engine holds ~3.5 GB each; extract needs 10-14 GB of
    # the 24 GB WSL cap. Stop everything, every profile.
    subprocess.run(["docker", "compose", "--profile", "major", "--profile", "full", "stop"],
                   cwd=ROOT, check=True)
    scripts = ROOT / "scripts"
    inputs: list[str] = []
    for cc, (raw, major) in COUNTRIES.items():
        if not (DATA / raw).exists():
            raise SystemExit(f"missing extract for {cc}: data/{raw}")
        (DATA / major).parent.mkdir(parents=True, exist_ok=True)
        stage(DATA / major, f"{cc}: major-roads filter", TOOLS_IMAGE,
              "osmium", "tags-filter", "--overwrite", "-o", f"/data/{major}", f"/data/{raw}",
              MAJOR_FILTER, RESTRICTIONS, inputs=(DATA / raw,))
        connectors = f"build/{cc}/ferry-connectors.osm.pbf"
        stage(DATA / connectors, f"{cc}: ferry connectors", TOOLS_IMAGE,
              "python", "/scripts/ferry_connectors.py", f"/data/{raw}", f"/data/build/{cc}",
              inputs=(DATA / raw, scripts / "ferry_connectors.py"))
        inputs += [major, connectors]
        if cc in NORTH_COUNTRIES:
            north = f"build/{cc}/north-{NORTH_LAT}.osm.pbf"
            stage(DATA / north, f"{cc}: north of {NORTH_LAT}N", TOOLS_IMAGE,
                  "osmium", "extract", "--overwrite", "-b", f"-180,{NORTH_LAT},-50,85",
                  "-o", f"/data/{north}", f"/data/{raw}", inputs=(DATA / raw,))
            north_minor = f"build/{cc}/north-{NORTH_LAT}-minor.osm.pbf"
            stage(DATA / north_minor, f"{cc}: north minor roads", TOOLS_IMAGE,
                  "osmium", "tags-filter", "--overwrite", "-o", f"/data/{north_minor}",
                  f"/data/{north}", NORTH_FILTER, RESTRICTIONS, inputs=(DATA / north,))
            inputs.append(north_minor)

    OUT.mkdir(exist_ok=True)
    out = "/data/osrm-na-ferry"
    in_paths = tuple(DATA / p for p in inputs)
    merged, dedup = OUT / "na-merged.osm.pbf", OUT / "na-dedup.osm.pbf"
    car_pbf = OUT / "na-carferries.osm.pbf"
    pbf = OUT / f"{NAME}.osm.pbf"
    osrm = OUT / f"{NAME}.osrm"
    stage(merged, "merge", TOOLS_IMAGE, "osmium", "merge", "--overwrite",
          "-o", f"{out}/{merged.name}", *[f"/data/{p}" for p in inputs], inputs=in_paths)
    # Extracts from different days carry different versions of the same
    # border-area objects; merge keeps both (582 duplicate nodes in Oct 2026).
    # time-filter with no time keeps the newest version of each.
    stage(dedup, "dedup", TOOLS_IMAGE, "osmium", "time-filter", "--overwrite",
          "-o", f"{out}/{dedup.name}", f"{out}/{merged.name}", inputs=(merged,))
    borders = OUT / "na-borders.osm.pbf"
    stage(borders, "border crossings", TOOLS_IMAGE, "python", "/scripts/border_crossings.py",
          f"{out}/{dedup.name}", f"{out}/{borders.name}", f"{out}/borders",
          *[f"/data/{raw}" for raw, _ in COUNTRIES.values()],
          inputs=(dedup, scripts / "border_crossings.py"))
    # Drop rail, freight, tour and foot-passenger ferries (car_ferries.py).
    stage(car_pbf, "car ferries only", TOOLS_IMAGE, "python", "/scripts/car_ferries.py",
          f"{out}/{borders.name}", f"{out}/{car_pbf.name}", f"{out}/ferries",
          inputs=(borders, scripts / "car_ferries.py"))
    # Approach stubs that copy the whole crossing's duration (Seattle-Bremerton
    # charged its hour twice) and MM:SS typos read as hours (ferry_durations.py).
    stage(pbf, "ferry durations", TOOLS_IMAGE, "python", "/scripts/ferry_durations.py",
          f"{out}/{car_pbf.name}", f"{out}/{pbf.name}", f"{out}/ferries",
          inputs=(car_pbf, scripts / "ferry_durations.py"))
    profile = ROOT / "profiles" / "car.lua"
    stage(osrm.with_suffix(".osrm.fileIndex"), "extract", OSRM_IMAGE,
          "osrm-extract", "-p", "/opt/car.lua", "--threads", THREADS,
          f"/data/osrm-na-ferry/{pbf.name}", inputs=(pbf, profile))
    stage(osrm.with_suffix(".osrm.partition"), "partition", OSRM_IMAGE,
          "osrm-partition", "--threads", THREADS, f"/data/osrm-na-ferry/{osrm.name}",
          inputs=(osrm.with_suffix(".osrm.fileIndex"),))
    stage(osrm.with_suffix(".osrm.cell_metrics"), "customize", OSRM_IMAGE,
          "osrm-customize", "--threads", THREADS, f"/data/osrm-na-ferry/{osrm.name}",
          inputs=(osrm.with_suffix(".osrm.partition"),))
    print(f"\nready: {osrm}")


if __name__ == "__main__":
    main()
