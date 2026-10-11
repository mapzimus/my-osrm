"""Ferry + dock-connector overlay (data/app/ferries.geojson) from the ferry build artifacts.

Reads data/build/<cc>/ferry-connectors.osm.pbf (lines) and ferry-report.json
(endpoint status) with pyosmium inside the tools container.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app import config

OUT = config.APP_DATA / "ferries.geojson"

CONTAINER_SCRIPT = r'''
import json, sys, glob, os
import osmium
feats = []
class H(osmium.SimpleHandler):
    def __init__(self, cc): super().__init__(); self.cc = cc
    def way(self, w):
        try:
            coords = [[round(n.lon, 6), round(n.lat, 6)] for n in w.nodes]
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2: return
        kind = "ferry" if w.tags.get("route") == "ferry" else "connector"
        feats.append({"type": "Feature", "properties": {"kind": kind, "id": w.id, "cc": self.cc,
                      "name": w.tags.get("name", ""), "highway": w.tags.get("highway", "")},
                      "geometry": {"type": "LineString", "coordinates": coords}})
for d in sorted(glob.glob("/data/build/*")):
    cc = os.path.basename(d)
    pbf = os.path.join(d, "ferry-connectors.osm.pbf")
    if not os.path.exists(pbf): continue
    H(cc).apply_file(pbf, locations=True)
    rep = os.path.join(d, "ferry-report.json")
    if os.path.exists(rep):
        for e in json.load(open(rep))["endpoints"]:
            feats.append({"type": "Feature", "properties": {"kind": "endpoint", "cc": cc, "name": e.get("name", ""),
                          "status": e.get("status"), "node": e.get("node"), "connector_m": e.get("connector_m")},
                          "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]}})
    print(cc, len(feats), "features so far", flush=True)
tmp = "/data/app/ferries.geojson.tmp"
json.dump({"type": "FeatureCollection", "features": feats}, open(tmp, "w"))
os.replace(tmp, "/data/app/ferries.geojson")
print("wrote", len(feats), "features")
'''


def run(scratch: Path) -> Path:
    if OUT.exists():
        print(f"[skip] ferry overlay ({OUT.stat().st_size / 1e6:.1f} MB)")
        return OUT
    if not any((config.DATA / "build").glob("*/ferry-connectors.osm.pbf")):
        raise RuntimeError("no data/build/<cc>/ferry-connectors.osm.pbf; run scripts/build_ferry_engine.py first")
    config.APP_DATA.mkdir(parents=True, exist_ok=True)
    script = scratch / "ferries_build.py"
    script.write_text(CONTAINER_SCRIPT)
    cmd = ["docker", "run", "--rm", "-v", f"{config.DATA}:/data", "-v", f"{scratch}:/scratch:ro",
           config.TOOLS_IMAGE, "python", "/scratch/ferries_build.py"]
    print("+ docker run my-osrm-tools python ferries_build.py", flush=True)
    subprocess.run(cmd, check=True)
    print(f"[done] ferry overlay -> {OUT}")
    return OUT
