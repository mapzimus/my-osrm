"""Offline place index: osmium tags-filter each raw extract down to place nodes,
then build places.sqlite inside the tools container (pyosmium)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app import config

EXTRACTS = {"us": "us-latest.osm.pbf", "ca": "canada-latest.osm.pbf", "mx": "mexico-latest.osm.pbf",
            "cam": "central-america-latest.osm.pbf"}
OUT = config.APP_DATA / "places.sqlite"


def docker(*args: str) -> None:
    cmd = ["docker", "run", "--rm", "-v", f"{config.DATA}:/data",
           "-v", f"{config.ROOT / 'app' / 'setup'}:/setup:ro", config.TOOLS_IMAGE, *args]
    print("+", " ".join(cmd[-len(args):]), flush=True)
    subprocess.run(cmd, check=True)


def run(scratch: Path) -> Path:
    if OUT.exists():
        print(f"[skip] places index ({OUT.stat().st_size / 1e6:.0f} MB)")
        return OUT
    config.APP_DATA.mkdir(parents=True, exist_ok=True)
    args = []
    for cc, raw in EXTRACTS.items():
        if not (config.DATA / raw).exists():
            print(f"[warn] no extract for {cc}: data/{raw} (skipping)")
            continue
        filtered = config.APP_DATA / f"places-{cc}.osm.pbf"
        done = filtered.with_name(filtered.name + ".done")
        if not (filtered.exists() and done.exists()):
            docker("osmium", "tags-filter", "--overwrite", "-o", f"/data/app/{filtered.name}", f"/data/{raw}",
                   "n/place=city,town,village")
            done.touch()
        else:
            print(f"[skip] {cc}: place nodes filtered")
        args.append(f"{cc}=/data/app/{filtered.name}")
    if not args:
        raise RuntimeError("no raw extracts in data/; download them first (scripts/update_data.py)")
    docker("python", "/setup/places_build.py", "/data/app/places.sqlite", *args)
    print(f"[done] places index -> {OUT}")
    return OUT
