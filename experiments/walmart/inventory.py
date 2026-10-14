"""Every Walmart store in the 50 states and Puerto Rico, one point each
(Max, 2026-10-14: all store types, not Sam's Club).

From OSM (extract.sh): features with brand:wikidata=Q483551 or brand=Walmart.
A store is a shop (department store, supermarket, general, variety) that is
not a fuel station, pharmacy, vision or auto centre, pickup locker or closed.
One store mapped twice (building and point) within DEDUPE_M is kept once,
the polygon preferred.

    python experiments/walmart/inventory.py

Writes cache/stores.json; prints the count beside Walmart's official count
and stops (exit 1) if OSM is short by more than GATE.
"""

from __future__ import annotations

import collections
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import shape

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
SHOPS = {"department_store", "supermarket", "general", "variety_store"}
NOT_STORE = ("fuel", "pharmacy", "vision", "auto care", "tire", "pickup", "money center", "sam's")
DEDUPE_M = 150
# Walmart U.S. stores incl. Puerto Rico (Supercenters + Discount + Neighborhood
# Markets). corporate.walmart.com/about/location-facts/united-states, units as of
# 2026-07-31 (read 2026-10-14): 3,569 Supercenters + 351 discount + 674 Neighborhood
# Markets = 4,594 (excludes 21 small formats and 602 Sam's Club).
OFFICIAL = 4_594
GATE = 0.02


def is_store(p: dict) -> bool:
    name = (p.get("name") or "").lower()
    if p.get("shop") not in SHOPS or p.get("amenity") or p.get("opening_hours") == "closed":
        return False
    return not any(w in name for w in NOT_STORE)


def kind(name: str) -> str:
    n = name.lower()
    return "market" if "neighborhood market" in n else "supercenter" if "supercenter" in n else "store"


def dedupe(stores: list[dict], m: float = DEDUPE_M) -> list[dict]:
    """Keep one store per cluster closer than m metres; polygons (area=1) first."""
    stores = sorted(stores, key=lambda s: -s.get("area", 0))
    k = math.cos(math.radians(38))
    xy = np.array([(s["lon"] * k * 111_200, s["lat"] * 111_200) for s in stores])
    tree, gone, out = cKDTree(xy), set(), []
    for i, s in enumerate(stores):
        if i in gone:
            continue
        out.append(s)
        gone.update(tree.query_ball_point(xy[i], m))
    return out


def main() -> None:
    stores = []
    for region in ("us", "puerto-rico"):
        for f in json.load(open(CACHE / f"osm_{region}.geojson", encoding="utf-8"))["features"]:
            p = f["properties"]
            if not is_store(p):
                continue
            g = shape(f["geometry"])
            pt = g.representative_point()
            stores.append(dict(name=p.get("name") or "Walmart", kind=kind(p.get("name") or ""),
                               lon=round(pt.x, 6), lat=round(pt.y, 6), region=region,
                               osm=str(p.get("@id", f.get("id", ""))), area=int(g.geom_type != "Point")))
    n_raw = len(stores)
    stores = dedupe(stores)
    for s in stores:
        s.pop("area")
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    by = collections.Counter(s["kind"] for s in stores)
    short = 1 - len(stores) / OFFICIAL
    print(f"{len(stores)} stores ({n_raw - len(stores)} duplicates merged): {dict(by)}; "
          f"official {OFFICIAL:,}, OSM {'short' if short > 0 else 'over'} by {abs(short):.1%}")
    if short > GATE:
        sys.exit(f"STOP: OSM is missing more than {GATE:.0%} of stores. Report to Max before building.")


if __name__ == "__main__":
    main()
