"""Every Dunkin' and Starbucks in the lower 48, one point each.

From OSM (extract.sh): brand:wikidata Q847743 (Dunkin') / Q37158 (Starbucks),
or brand / name. Kept: places that sell coffee (cafe, fast food, restaurant,
or a shop); not offices, closed or disused places. One store mapped twice
(building and point) within DEDUPE_M is kept once, per chain.

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
WIKIDATA = {"Q847743": "dunkin", "Q37158": "starbucks"}
FOOD = {"cafe", "fast_food", "restaurant", "ice_cream", "food_court"}
DEDUPE_M = 75
STATE_SLOP_DEG = 0.05          # piers and coastline points just outside a state polygon


def chain(p: dict) -> str | None:
    if p.get("brand:wikidata") in WIKIDATA:
        return WIKIDATA[p["brand:wikidata"]]
    for key in ("brand", "name"):
        v = (p.get(key) or "").lower().replace("’", "'")
        if v.startswith("dunkin"):
            return "dunkin"
        if v.startswith("starbucks"):
            return "starbucks"
    return None


def is_store(p: dict) -> bool:
    if p.get("office") or p.get("opening_hours") == "closed":
        return False
    if any(k.split(":")[0] in ("disused", "was", "abandoned", "demolished", "removed") for k in p):
        return False
    return p.get("amenity") in FOOD or bool(p.get("shop"))


def dedupe(stores: list[dict], m: float = DEDUPE_M) -> list[dict]:
    """Keep one store per cluster closer than m metres; polygons (area=1) first."""
    stores = sorted(stores, key=lambda s: -s["area"])
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
    """(STRtree, names) of the 48 contiguous states plus DC."""
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
    stores, why = [], collections.Counter()
    for f in json.load(open(CACHE / "osm_us.geojson", encoding="utf-8"))["features"]:
        if f["geometry"]["type"] == "LineString":      # osmium exports a building twice: outline and area
            continue
        p = f["properties"]
        c = chain(p)
        if not c:
            why["not a chain"] += 1
            continue
        if not is_store(p):
            why[f"{c}: not a store"] += 1
            continue
        g = shape(f["geometry"])
        pt = g.representative_point()
        i = int(tree.nearest(pt))
        if geoms[i].distance(pt) > STATE_SLOP_DEG:
            why[f"{c}: outside lower 48"] += 1
            continue
        stores.append(dict(chain=c, name=p.get("name") or p.get("brand") or "", lon=round(pt.x, 6),
                           lat=round(pt.y, 6), state=names[i],
                           area=int(g.geom_type != "Point")))
    out = []
    for c in ("dunkin", "starbucks"):
        mine = [s for s in stores if s["chain"] == c]
        kept = dedupe(mine)
        why[f"{c}: duplicate"] += len(mine) - len(kept)
        out += kept
    for s in out:
        s.pop("area")

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
    print(f"Dunkin': {by['dunkin']:,}   Starbucks: {by['starbucks']:,}")
    print("dropped:", dict(sorted(why.items())))
    for c in ("dunkin", "starbucks"):
        top = collections.Counter(s["state"] for s in out if s["chain"] == c).most_common(5)
        print(f"  {c} top states:", ", ".join(f"{st} {n:,}" for st, n in top))


if __name__ == "__main__":
    main()
