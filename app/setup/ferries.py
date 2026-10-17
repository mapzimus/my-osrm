"""Ferry + dock-connector overlay (data/app/ferries.geojson) for the Inspect tab.

Preferred source: data/osrm-na-ferry/na-ferry.osm.pbf, the exact file the
engine was extracted from (kept since the 2026-10-12 build). The overlay then
shows what the engine routes on:
  - every route=ferry way (car ferries only, durations already fixed;
    `duration_fix` says which ones ferry_durations.py changed)
  - every minor road (not in the major-road filter) inside the 6 km dock box,
    i.e. the roads kept only to reach a ferry. Turn-restriction member ways and
    unclassified roads at 55N+ are excluded: the build keeps those for other reasons.
    A connector that is also a restriction member is missed (rare).
  - dock endpoints with their connector status from data/build/*/ferry-report.json

Fallback when that file is missing: data/build/*/ferry-connectors.osm.pbf, as before.
Both run with pyosmium inside the tools container.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app import config

OUT = config.APP_DATA / "ferries.geojson"
NETWORK = config.DATA / "osrm-na-ferry" / "na-ferry.osm.pbf"

ENDPOINTS = r'''
def endpoints():
    out = []
    for d in sorted(glob.glob("/data/build/*")):
        rep = os.path.join(d, "ferry-report.json")
        if not os.path.exists(rep): continue
        cc = os.path.basename(d)
        for e in json.load(open(rep))["endpoints"]:
            out.append({"type": "Feature", "properties": {"kind": "endpoint", "cc": cc, "name": e.get("name", ""),
                        "status": e.get("status"), "node": e.get("node"), "connector_m": e.get("connector_m")},
                        "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]}})
    return out
def write(feats):
    tmp = "/data/app/ferries.geojson.tmp"
    json.dump({"type": "FeatureCollection", "features": feats}, open(tmp, "w"))
    os.replace(tmp, "/data/app/ferries.geojson")
    kinds = {}
    for f in feats: kinds[f["properties"]["kind"]] = kinds.get(f["properties"]["kind"], 0) + 1
    print("wrote", kinds, flush=True)
'''

FROM_NETWORK = r'''
import json, glob, os, math
import osmium
''' + ENDPOINTS + r'''
MAJOR = {"motorway", "trunk", "primary", "secondary", "tertiary",
         "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
BOX = 0.06
eps = endpoints()
cells = {}
for e in eps:
    x, y = e["geometry"]["coordinates"]
    cells.setdefault((math.floor(x / BOX), math.floor(y / BOX)), []).append((x, y))
def near_dock(lon, lat):
    cx, cy = math.floor(lon / BOX), math.floor(lat / BOX)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for x, y in cells.get((cx + dx, cy + dy), ()):
                if abs(lon - x) <= BOX and abs(lat - y) <= BOX:
                    return True
    return False
# Minor roads that are only in the network as turn-restriction members (the
# major filter keeps r/type=restriction with its member ways) aren't dock access.
class R(osmium.SimpleHandler):
    def __init__(self): super().__init__(); self.ids = set()
    def relation(self, r):
        if r.tags.get("type") == "restriction":
            self.ids.update(m.ref for m in r.members if m.type == "w")
rel = R(); rel.apply_file("/data/osrm-na-ferry/na-ferry.osm.pbf")
print("restriction member ways:", len(rel.ids), flush=True)
NORTH_LAT = 55          # build_ferry_engine.py keeps all unclassified roads up here
feats = []
class H(osmium.SimpleHandler):
    def way(self, w):
        t = w.tags
        ferry = t.get("route") == "ferry"
        hw = t.get("highway")
        if not ferry and (not hw or hw in MAJOR or w.id in rel.ids):
            return
        try:
            coords = [[round(n.lon, 6), round(n.lat, 6)] for n in w.nodes]
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2:
            return
        if not ferry:
            if hw == "unclassified" and coords[0][1] >= NORTH_LAT:
                return
            mid = coords[len(coords) // 2]
            if not (near_dock(*coords[0]) or near_dock(*coords[-1]) or near_dock(*mid)):
                return
        props = {"kind": "ferry" if ferry else "connector", "id": w.id, "name": t.get("name", ""),
                 "highway": hw or ""}
        if ferry:
            props["duration"] = t.get("duration", "")
            props["duration_fix"] = t.get("my_osrm:duration_fix", "")
        elif t.get("my_osrm:dock_walkway"):
            props["dock_walkway"] = True
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "LineString", "coordinates": coords}})
H().apply_file("/data/osrm-na-ferry/na-ferry.osm.pbf", locations=True, idx="flex_mem")
write(feats + eps)
'''

FROM_CONNECTORS = r'''
import json, glob, os
import osmium
''' + ENDPOINTS + r'''
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
    pbf = os.path.join(d, "ferry-connectors.osm.pbf")
    if os.path.exists(pbf):
        H(os.path.basename(d)).apply_file(pbf, locations=True)
write(feats + endpoints())
'''


SRC = config.APP_DATA / "ferries.src.json"     # which network file the overlay was built from


def fingerprint() -> dict | None:
    """Identity of the engine network the overlay is built from: name, size and date. A
    rebuild changes both numbers (VM mtimes travel with the files, so "newer than" is
    not used, only "different")."""
    if NETWORK.exists() and NETWORK.stat().st_size > 0:
        s = NETWORK.stat()
        return {"source": NETWORK.name, "size": s.st_size, "mtime": int(s.st_mtime)}
    return None


def stale() -> bool:
    """True when the overlay exists but the engine network changed since it was built, i.e.
    after an engine rebuild: run `python -m app.setup --only ferries` (and restart the app)."""
    if not OUT.exists() or not SRC.exists():
        return False
    try:
        return json.loads(SRC.read_text()) != fingerprint()
    except (OSError, ValueError):
        return False


def run(scratch: Path) -> Path:
    fp = fingerprint()
    if OUT.exists():
        if not SRC.exists():
            # Built before the fingerprint existed (2026-10-17): assume the current network.
            if fp:
                SRC.write_text(json.dumps(fp))
            print(f"[skip] ferry overlay ({OUT.stat().st_size / 1e6:.1f} MB); delete it to rebuild")
            return OUT
        if not stale():
            print(f"[skip] ferry overlay ({OUT.stat().st_size / 1e6:.1f} MB) matches {NETWORK.name}")
            return OUT
        print(f"[redo] {NETWORK.name} changed since the overlay was built (engine rebuild): rebuilding")
    use_network = NETWORK.exists() and NETWORK.stat().st_size > 0
    if not use_network and not any((config.DATA / "build").glob("*/ferry-connectors.osm.pbf")):
        raise RuntimeError("no engine network (data/osrm-na-ferry/na-ferry.osm.pbf) and no "
                           "data/build/<cc>/ferry-connectors.osm.pbf; build the ferry engine first")
    config.APP_DATA.mkdir(parents=True, exist_ok=True)
    script = scratch / "ferries_build.py"
    script.write_text(FROM_NETWORK if use_network else FROM_CONNECTORS)
    print(f"[run]  ferry overlay from {'the engine network ' + NETWORK.name if use_network else 'connector files'}",
          flush=True)
    cmd = ["docker", "run", "--rm", "-v", f"{config.DATA}:/data", "-v", f"{scratch}:/scratch:ro",
           config.TOOLS_IMAGE, "python", "/scratch/ferries_build.py"]
    subprocess.run(cmd, check=True)
    if fp:
        SRC.write_text(json.dumps(fp))
    print(f"[done] ferry overlay -> {OUT}")
    return OUT
