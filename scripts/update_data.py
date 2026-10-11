"""Refresh raw OSM extracts from Geofabrik, then rebuild the engine.

    python scripts/update_data.py us ca        # refresh US + Canada, rebuild
    python scripts/update_data.py mx --no-build

For each country: download to <file>.part (resumable), check the byte count
against the server and that osmium can read the whole file, then swap it in
and delete everything derived from the old extract (its major-roads PBF and
data/build/<cc>/) plus the engine build, so build_ferry_engine.py redoes
exactly the stale stages. A failed download never touches working data.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
from build_ferry_engine import COUNTRIES, OUT, TOOLS_IMAGE  # noqa: E402

GEOFABRIK = "https://download.geofabrik.de"
# Download folder per country code. Geofabrik for everything it publishes
# (Central America is top-level, Puerto Rico sits under us/); Bermuda only
# exists on the openstreetmap.fr mirror.
SOURCE = {
    "us": f"{GEOFABRIK}/north-america",
    "ca": f"{GEOFABRIK}/north-america",
    "mx": f"{GEOFABRIK}/north-america",
    "cam": GEOFABRIK,
    "pr": f"{GEOFABRIK}/north-america/us",
    "bm": "https://download.openstreetmap.fr/extracts/north-america",
    "gl": f"{GEOFABRIK}/north-america",
}


def remote_size(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return int(resp.headers["Content-Length"])
        except OSError as exc:  # timeouts and resets are transient on big mirrors
            print(f"  HEAD failed ({exc}); retry {attempt + 1}/5", flush=True)
            time.sleep(10 * (attempt + 1))
    raise SystemExit(f"could not reach {url}")


def download(cc: str) -> None:
    raw = COUNTRIES[cc][0]
    url = f"{SOURCE[cc]}/{raw}"
    dest, part = DATA / raw, DATA / f"{raw}.part"
    size = remote_size(url)
    print(f"[{cc}] {raw}: {size / 1e9:.2f} GB", flush=True)
    # curl resumes a partial .part from an earlier interrupted run; a complete
    # one is skipped (resuming a finished file makes the server answer 416).
    if not (part.exists() and part.stat().st_size == size):
        subprocess.run(["curl", "-L", "--fail", "-C", "-", "-sS", "-o", str(part), url], check=True)
    if part.stat().st_size != size:
        raise SystemExit(f"[{cc}] size mismatch: got {part.stat().st_size}, expected {size}")
    # -e reads every block, so truncation or corruption fails here, not mid-build.
    subprocess.run(["docker", "run", "--rm", "-v", f"{DATA}:/data", TOOLS_IMAGE,
                    "osmium", "fileinfo", "-e", "-F", "pbf", f"/data/{part.name}"],
                   check=True, stdout=subprocess.DEVNULL)
    part.replace(dest)

    major = DATA / COUNTRIES[cc][1]
    major.unlink(missing_ok=True)
    shutil.rmtree(DATA / "build" / cc, ignore_errors=True)
    print(f"[{cc}] swapped in; cleared {major.name} and build/{cc}/", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("countries", nargs="+", choices=COUNTRIES)
    ap.add_argument("--no-build", action="store_true")
    args = ap.parse_args()

    for cc in args.countries:
        download(cc)
    if args.no_build:
        return
    subprocess.run(["docker", "compose", "stop", "na-ferry"], cwd=ROOT, check=True)
    shutil.rmtree(OUT, ignore_errors=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_ferry_engine.py")], check=True)
    subprocess.run(["docker", "compose", "up", "-d", "na-ferry"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
