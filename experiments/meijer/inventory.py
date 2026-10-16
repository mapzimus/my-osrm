"""Every Meijer (viewer request, 2026-10-16: "do every Meijer in the US!"), one drive (Optimal Drives).

Two sources, merged (data probe 2026-10-16):
- AllThePlaces meijer_us (Meijer's own locator, run in cache/atp/run.txt): 205 stores, but the
  spider logged 74 errors and Meijer says "260+"; it missed stores like Westerville OH, East
  Lansing MI, Royal Oak MI, Milford OH, Oswego IL, Rolling Meadows IL, Bolingbrook IL.
- OpenStreetMap (osmium tags-filter brand:wikidata=Q1917753 / name=Meijer on us-latest.osm.pbf ->
  data/scratch/meijer.geojson): 538 shop=supermarket features, 275 stores once a store's building
  and point (within MERGE_KM) are one.
A store is the ATP point where the two agree (within MERGE_KM), else the OSM one. Result: 279
(198 both, 76 OSM only, 5 ATP only), in line with "260+" and the openings since. Meijer's
own site blocks scripted access, so the OSM-only ones can't be checked one by one there.

    python experiments/meijer/inventory.py

Writes cache/stores.json (name, town, state, lon, lat) for walmart/build.py --cache.
"""

from __future__ import annotations

import collections
import json
import math
from pathlib import Path

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from shapely.geometry import shape

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
OSM = HERE.parents[1] / "data" / "scratch" / "meijer.geojson"
MERGE_KM = 0.6
STATES = {"MI", "OH", "IN", "IL", "KY", "WI"}


def km(a, b) -> float:
    return math.hypot((a[0] - b[0]) * 111.32 * math.cos(math.radians(a[1])), (a[1] - b[1]) * 110.57)


def main() -> None:
    atp = []
    for f in json.load(open(CACHE / "atp" / "meijer_us.geojson", encoding="utf-8"))["features"]:
        p = f["properties"]
        if p.get("shop") == "supermarket" and f.get("geometry"):
            lon, lat = f["geometry"]["coordinates"]
            atp.append(dict(lon=lon, lat=lat, town=f"{p.get('addr:city', '').strip()}, {p.get('addr:state', '')}",
                            state=p.get("addr:state", ""), ref=f"atp{p['ref:meijer']}", src="atp"))
    pts = []
    for f in json.load(open(OSM, encoding="utf-8"))["features"]:
        p = f["properties"]
        if p.get("shop") == "supermarket":
            c = shape(f["geometry"]).representative_point()
            pts.append((c.x, c.y, p, f.get("id")))
    xy = np.array([(x * 111.32 * math.cos(math.radians(y)), y * 110.57) for x, y, _, _ in pts])
    groups = collections.defaultdict(list)
    for k, q in zip(fcluster(linkage(xy, "single"), MERGE_KM, "distance"), pts):
        groups[k].append(q)
    osm = []
    for v in groups.values():
        p = next((q[2] for q in v if q[2].get("addr:city")), v[0][2])
        osm.append(dict(lon=float(np.mean([q[0] for q in v])), lat=float(np.mean([q[1] for q in v])),
                        town=f"{p.get('addr:city', '')}, {p.get('addr:state', '')}".strip(", "),
                        state=p.get("addr:state", ""), ref=f"osm{v[0][3]}", src="osm"))
    stores = list(atp)
    for o in osm:
        if min(km((o["lon"], o["lat"]), (a["lon"], a["lat"])) for a in atp) > MERGE_KM:
            stores.append(o)
    # OSM points without an address: state from the nearest store with one (all six are contiguous)
    for s in stores:
        if s["state"] not in STATES:
            near = min((t for t in stores if t["state"] in STATES), key=lambda t: km((s["lon"], s["lat"]), (t["lon"], t["lat"])))
            s["state"] = near["state"]
            if not s["town"].strip(", "):
                s["town"] = f"near {near['town']}"
    for k, s in enumerate(sorted(stores, key=lambda s: (s["state"], s["town"]))):
        s["name"] = f"Meijer #{k + 1}"
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    by = collections.Counter(s["state"] for s in stores)
    src = collections.Counter(s["src"] for s in stores)
    print(f"{len(stores)} Meijers in {len(by)} states {by.most_common()}; sources {dict(src)}")


if __name__ == "__main__":
    main()
