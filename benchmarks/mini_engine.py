"""Build a tiny engine with the REAL pipeline and profile, then assert on it.

Cuts a few small boxes out of the raw extracts (same file names as data/),
points build_ferry_engine.py at that folder via MY_OSRM_DATA, serves the
result on :5099 and checks the things a full build is too slow to iterate on:
border charge, ferry boarding, ferry speed, turn restrictions present, the
ungated dock road winning the merge, streets along the border uncharged.

    python benchmarks/mini_engine.py            # ~5 min
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MINI = ROOT / "data" / "scratch" / "mini"
RAW = ROOT / "data"
PY = sys.executable
TOOLS = "my-osrm-tools"
OSRM = ("ghcr.io/project-osrm/osrm-backend@sha256:"
        "aa6a1de3a71dafffd0ba39340542524f66e6841fc19bf7874a0e6a7967837f56")
PORT = 5099

# box name -> (west, south, east, north); countries whose extract covers it
BOXES = {
    "san_diego_tijuana": ((-117.30, 32.45, -116.85, 32.80), ("us", "mx")),
    "lake_champlain": ((-73.60, 44.55, -73.20, 44.85), ("us",)),
    "puget_sound": ((-122.80, 47.45, -122.25, 47.75), ("us",)),
    "cape_may_lewes": ((-75.20, 38.70, -74.85, 39.00), ("us",)),
    "derby_line": ((-72.20, 44.95, -72.00, 45.05), ("us", "ca")),
}
EXTRACTS = {"us": "us-latest.osm.pbf", "ca": "canada-latest.osm.pbf", "mx": "mexico-latest.osm.pbf"}
# The other COUNTRIES entries need a file too; an empty extract is fine.
EMPTY = ["central-america-latest.osm.pbf", "puerto-rico-latest.osm.pbf",
         "bermuda-latest.osm.pbf", "greenland-latest.osm.pbf"]


def sh(*args, **kw):
    print("+", " ".join(str(a) for a in args), flush=True)
    return subprocess.run([str(a) for a in args], check=True, **kw)


def tools(*args):
    return sh("docker", "run", "--rm", "-v", f"{RAW}:/raw:ro", "-v", f"{MINI}:/mini", TOOLS, *args)


def cut_outs() -> None:
    """One osmium pass per country (a config file lists every box): each pass
    scans the whole 12 GB US extract, so five separate passes took ~50 min.
    Cut-outs are kept between runs; delete data/scratch/mini to redo them."""
    MINI.mkdir(parents=True, exist_ok=True)
    for cc, raw in EXTRACTS.items():
        if (MINI / raw).exists():
            print(f"cut-outs for {cc} already present", flush=True)
            continue
        extracts = [{"output": f"part-{cc}-{name}.osm.pbf", "output_format": "pbf",
                     "bbox": list(bbox)}
                    for name, (bbox, ccs) in BOXES.items() if cc in ccs]
        (MINI / f"cut-{cc}.json").write_text(json.dumps(
            {"directory": "/mini", "extracts": extracts}))
        tools("osmium", "extract", "--overwrite", "-s", "complete_ways",
              "-c", f"/mini/cut-{cc}.json", f"/raw/{raw}")
        tools("osmium", "merge", "--overwrite", "-o", f"/mini/{raw}",
              *[f"/mini/{e['output']}" for e in extracts])
    for raw in EMPTY:
        if not (MINI / raw).exists():
            tools("osmium", "cat", "--overwrite", "-o", f"/mini/{raw}",
                  "/mini/part-us-lake_champlain.osm.pbf")   # any valid file; contents irrelevant


def build() -> None:
    for p in MINI.glob("**/*.done"):
        p.unlink()
    sh(PY, ROOT / "scripts" / "build_ferry_engine.py",
       env={**os.environ, "MY_OSRM_DATA": str(MINI), "OSRM_THREADS": "4"})


def serve() -> None:
    subprocess.run(["docker", "rm", "-f", "mini-osrm"], capture_output=True)
    sh("docker", "run", "-d", "--name", "mini-osrm", "-p", f"127.0.0.1:{PORT}:5000",
       "-v", f"{MINI / 'osrm-na-ferry'}:/data:ro", OSRM,
       "osrm-routed", "--algorithm", "mld", "/data/na-ferry.osrm")
    for _ in range(60):
        time.sleep(2)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/route/v1/driving/-73.3,44.7;-73.3,44.7", timeout=2)
            return
        except Exception:
            pass
    raise SystemExit("mini engine did not start")


def route(a, b):
    url = (f"http://127.0.0.1:{PORT}/route/v1/driving/{a[0]},{a[1]};{b[0]},{b[1]}"
           "?overview=false&steps=true")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        d = json.load(e)
    if d.get("code") != "Ok":
        return None
    r = d["routes"][0]
    ferry = any(s.get("mode") == "ferry" for leg in r["legs"] for s in leg["steps"])
    return r["distance"] / 1609.344, r["duration"] / 60, ferry


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}  {detail}", flush=True)
    return ok


def asserts() -> bool:
    out = MINI / "osrm-na-ferry"
    ok = True
    # Data-level
    info = subprocess.run(["docker", "run", "--rm", "-v", f"{MINI}:/mini:ro", TOOLS, "osmium",
                           "fileinfo", "-e", "/mini/osrm-na-ferry/na-ferry.osm.pbf"],
                          capture_output=True, text=True).stdout
    rels = int(next(l for l in info.splitlines() if "Number of relations" in l).split(":")[1])
    ok &= check("turn-restriction relations survive the filter", rels > 0, f"relations={rels}")
    borders = json.load(open(out / "borders" / "border-report.json", encoding="utf-8"))
    # Canusa Avenue/Street (US side) has the border as its centerline: never a
    # crossing. Rue Canusa (QC-247) ENDS at the border at the Beebe Plain port
    # of entry, west of -72.13: that one is a real customs stop.
    along = [b for b in borders if "canusa" in b["name"].lower()
             and not (b["name"].startswith("Rue") and b["lon"] < -72.13)]
    ok &= check("street along the border not charged (Canusa Ave)", not along, f"tagged={along}")
    conn = set(open(MINI / "build" / "us" / "connector-ids.txt").read().split())
    gated = subprocess.run(["docker", "run", "--rm", "-v", f"{MINI}:/mini:ro", TOOLS, "sh", "-c",
                            "osmium tags-filter -o /tmp/c.osm.pbf /mini/osrm-na-ferry/na-ferry.osm.pbf "
                            "w/name='Cape May-Lewes Ferry Road' 2>/dev/null; osmium cat -f opl /tmp/c.osm.pbf "
                            "| grep 'access=destination' | cut -d' ' -f1"],
                           capture_output=True, text=True).stdout.split()
    still = [w for w in gated if w in conn]
    ok &= check("Cape May-Lewes dock-path segments ungated (P1 version bump)", not still,
                f"gated on path={still}; gated off path={[w for w in gated if w not in conn]}")

    # Route-level
    tj = route((-117.0611, 32.5800), (-117.0382, 32.5149))      # Chula Vista -> Tijuana
    nc = route((-117.0611, 32.5800), (-117.0700, 32.6400))      # Chula Vista -> National City
    ok &= check("border charge ~20 min (Chula Vista->Tijuana vs ->National City)",
                tj and nc and 15 <= (tj[1] - nc[1]) <= 32, f"tj={tj} nc={nc}")
    gi = route((-73.3510, 44.7206), (-73.4529, 44.6995))        # Grand Isle -> Plattsburgh
    ok &= check("Grand Isle->Plattsburgh uses the ferry, ~55-75 min (boarding incl.)",
                gi and gi[2] and 50 <= gi[1] <= 80, f"{gi}")
    br = route((-122.3380, 47.6020), (-122.6250, 47.5620))      # Seattle Colman Dock -> Bremerton
    ok &= check("Seattle->Bremerton uses a ferry", br and br[2], f"{br}")
    cl = route((-74.9060, 38.9350), (-75.1400, 38.7745))        # Cape May -> Lewes
    # 85 min crossing + 30 boarding + ~15 driving; the stub-at-dock double
    # boarding showed up here as 178 min.
    ok &= check("Cape May->Lewes uses the ferry, 110-150 min", cl and cl[2] and cl[0] < 40
                and 110 <= cl[1] <= 150, f"{cl}")
    dl = route((-72.0990, 45.0030), (-72.0960, 45.0090))        # Derby Line VT -> Stanstead QC
    ok &= check("Derby Line->Stanstead charged once (~20-25 min)", dl and 18 <= dl[1] <= 30, f"{dl}")
    return ok


def main() -> None:
    cut_outs()
    build()
    serve()
    print("\n== asserts ==")
    ok = asserts()
    subprocess.run(["docker", "rm", "-f", "mini-osrm"], capture_output=True)
    print("\nMINI ENGINE:", "ALL PASS" if ok else "FAILURES ABOVE")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
