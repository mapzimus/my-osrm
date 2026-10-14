"""Every Dunkin' and Starbucks in the lower 48, one point each.

From AllThePlaces (fetch.sh): each chain's own store locator, so licensed
Starbucks (in Targets, airports, grocery stores) count too. OSM had ~70%
of stores. Points closer than DEDUPE_M (same chain) are one store listed twice.

    sh experiments/dunkin-vs-starbucks/fetch.sh
    python experiments/dunkin-vs-starbucks/stores.py

Writes cache/stores.geojson and cache/stores.csv (chain, name, lon, lat, state).
"""

from __future__ import annotations

import collections
import csv
import json
import math
from pathlib import Path

import numpy as np
import shapefile
from scipy.spatial import cKDTree
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = HERE / "cache"
# chain -> (AllThePlaces spider, brand:wikidata). Next Turf Wars: change this.
CHAINS = {"dunkin": ("dunkin_us", "Q847743"), "starbucks": ("starbucks_us", "Q37158")}
DEDUPE_M = 10
STATE_SLOP_DEG = 0.05          # piers and coastline points just outside a state polygon


def dedupe(stores: list[dict], m: float = DEDUPE_M) -> list[dict]:
    """Keep one store per cluster closer than m metres."""
    k = math.cos(math.radians(38))
    xy = np.array([(s["lon"] * k * 111_200, s["lat"] * 111_200) for s in stores])
    tree, gone, out = cKDTree(xy), set(), []
    for i, s in enumerate(stores):
        if i in gone:
            continue
        out.append(s)
        gone.update(tree.query_ball_point(xy[i], m))
    return out


def lower48():
    """STRtree, polygons and names of the 48 contiguous states plus DC."""
    sf = shapefile.Reader(str(ROOT / "data/naturalearth/ne_10m_admin_1_states_provinces"
                              / "ne_10m_admin_1_states_provinces.shp"))
    geoms, names = [], []
    for s, r in zip(sf.shapes(), sf.records()):
        if r["adm0_a3"] == "USA" and r["name"] not in ("Alaska", "Hawaii"):
            geoms.append(shape(s.__geo_interface__))
            names.append(r["name"])
    return STRtree(geoms), geoms, names


def main() -> None:
    tree, geoms, names = lower48()
    out, why = [], collections.Counter()
    for c, (spider, qid) in CHAINS.items():
        mine = []
        for f in json.load(open(CACHE / "atp" / f"{spider}.geojson", encoding="utf-8"))["features"]:
            p, g = f["properties"], f.get("geometry")
            if not g or p.get("brand:wikidata") != qid:
                why[f"{c}: no point or other brand"] += 1
                continue
            pt = Point(g["coordinates"][:2])
            i = int(tree.nearest(pt))
            if geoms[i].distance(pt) > STATE_SLOP_DEG:
                why[f"{c}: outside lower 48"] += 1
                continue
            mine.append(dict(chain=c, name=p.get("name") or p.get("brand") or "", lon=round(pt.x, 6),
                             lat=round(pt.y, 6), state=names[i]))
        kept = dedupe(mine)
        why[f"{c}: duplicate"] += len(mine) - len(kept)
        out += kept

    fields = ["chain", "name", "lon", "lat", "state"]
    with open(CACHE / "stores.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fields)
        w.writeheader()
        w.writerows(out)
    json.dump(dict(type="FeatureCollection", features=[
        dict(type="Feature", geometry=dict(type="Point", coordinates=[s["lon"], s["lat"]]),
             properties={k: s[k] for k in fields if k not in ("lon", "lat")}) for s in out]),
        open(CACHE / "stores.geojson", "w", encoding="utf-8"))

    by = collections.Counter(s["chain"] for s in out)
    print("  ".join(f"{c}: {by[c]:,}" for c in CHAINS))
    print("dropped:", dict(sorted(why.items())))
    for c in CHAINS:
        top = collections.Counter(s["state"] for s in out if s["chain"] == c).most_common(5)
        print(f"  {c} top states:", ", ".join(f"{st} {n:,}" for st, n in top))


if __name__ == "__main__":
    main()
