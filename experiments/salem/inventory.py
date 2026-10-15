"""Every Salem in the lower 48 (Max, 2026-10-15): one drive to all of them, Optimal Drives.

Any OSM place (city, town, village, hamlet) with a word starting "Salem" in its
name: Salem, New Salem, Old Salem, West Salem, Salem Center, Winston-Salem,
Salemburg (Max: "not opposed to a Salemburg"). Jerusalem doesn't count. Source: places.sh. State from the
Census TIGER outline. Two nodes with the same name within DEDUP_KM are one
place. Each place is snapped to the road network (OSRM /nearest); a big snap
distance is printed for checking.

    sh experiments/salem/places.sh
    python experiments/salem/inventory.py

Writes cache/stores.json (name, town, state, lon, lat) for walmart/build.py --cache.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
import shapefile
import shapely
from shapely.geometry import shape

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
OSRM = "http://127.0.0.1:5002"
SALEM = re.compile(r"(?<![A-Za-z])Salem", re.I)   # a word starting "Salem": Salemburg too (Max), not Jerusalem
DEDUP_KM = 3.0
# Subdivisions and oddities tagged as places (Max, 2026-10-15: "drop them, 162 Salems")
DROP = {("Ole Salem Estates", "SC"), ("Salem Acres", "NC"), ("Salem Meadows", "GA"), ("Salem Place", "GA"),
        ("Salem Woods", "DE"), ("Salem Woods", "NC"), ("Salem Cove", "GA"), ("Salem Village", "GA"),
        ("Old Salem Chautauqua", "IL"), ("Salem Coffey P O and Station", "MO")}
NOT_L48 = {"AK", "HI", "PR", "GU", "VI", "AS", "MP"}


def states():
    sf = shapefile.Reader(str(HERE.parent / "border_crossings" / "cache" / "states" / "tl_2024_us_state.shp"))
    out = []
    for s, r in zip(sf.shapes(), sf.records()):
        if r["STUSPS"] not in NOT_L48:
            g = shape(s.__geo_interface__)
            shapely.prepare(g)
            out.append((r["STUSPS"], g))
    return out


def main() -> None:
    st = states()
    found = []
    with open(CACHE / "places_us.geojsonseq", encoding="utf-8") as fh:
        for line in fh:
            if "alem" not in line:
                continue
            f = json.loads(line.lstrip("\x1e"))
            name = f["properties"].get("name", "")
            if not SALEM.search(name):
                continue
            lon, lat = f["geometry"]["coordinates"]
            state = next((code for code, g in st if shapely.contains_xy(g, lon, lat)), None)
            if state and (name, state) not in DROP:
                found.append(dict(name=name, state=state, place=f["properties"].get("place"), lon=lon, lat=lat))
    keep = []
    for p in sorted(found, key=lambda p: ["city", "town", "village", "hamlet"].index(p["place"])):
        k = np.cos(np.radians(p["lat"]))
        if all(not (q["name"] == p["name"] and np.hypot((q["lon"] - p["lon"]) * k, q["lat"] - p["lat"]) * 111.2
                    < DEDUP_KM) for q in keep):
            keep.append(p)
    stores = []
    for p in sorted(keep, key=lambda p: (p["state"], p["name"])):
        try:
            with urllib.request.urlopen(f"{OSRM}/nearest/v1/driving/{p['lon']:.6f},{p['lat']:.6f}", timeout=30) as r:
                snap = json.load(r)["waypoints"][0]["distance"]
        except OSError:
            snap = -1
        flag = "  <-- check" if snap > 1500 or snap < 0 else ""
        print(f"  {p['state']}  {p['name']:<28} {p['place']:<8} snap {snap:6.0f} m{flag}")
        stores.append(dict(name=p["name"], town=f"{p['name']}, {p['state']}", state=p["state"], kind=p["place"],
                           lon=p["lon"], lat=p["lat"]))
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    names = {}
    for s in stores:
        names[s["name"]] = names.get(s["name"], 0) + 1
    print(f"{len(stores)} Salems in {len({s['state'] for s in stores})} states; "
          + ", ".join(f"{n} {k}" for k, n in sorted(names.items(), key=lambda x: -x[1])[:12]))


if __name__ == "__main__":
    sys.exit(main())
