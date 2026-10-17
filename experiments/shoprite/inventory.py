"""Every ShopRite (viewer request, Ian on TikTok, 2026-10-17: "can you do Shoprite").

Source: AllThePlaces shoprite_us (Wakefern's store API; run in cache/atp/run.txt): 316 entries,
277 named "ShopRite" (the rest: 37 ShopRite Wines & Spirits / Liquors next door, 2 Grade A
Market in Stamford, a different banner); 2 are "Coming Soon!" (Hudson Exchange, Jersey City;
Nutmeg Plaza, Danbury) and are left out. Wakefern: "nearly 280 locations" in NJ, NY, PA, CT, DE, MD.

Positions, checked three ways (Max: double-check harder). The locator put the Fischer Bay store
(860 Fischer Blvd, Toms River) 6 km off, on top of the Route 37 one, so every store is checked:
- the Census batch geocoder on its address (cache/census_out.csv; 239 of 277 matched),
- OpenStreetMap's ShopRites (Overpass, brand:wikidata Q7501097 or name=ShopRite, cache/osm.json).
Pick, in order: an OSM ShopRite with the same house number and street within 10 km; the locator
point if an OSM ShopRite is within 0.3 km of it; the Census point if one is within 0.3 km of that;
the locator point if Census agrees within 0.5 km; else printed for a look (FIX: settled by hand).

    python experiments/shoprite/inventory.py

Writes cache/stores.json (name, town, state, lon, lat) for walmart/build.py --cache.
"""

from __future__ import annotations

import collections
import csv
import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
# Settled by hand (2026-10-17, Nominatim): the locator's 24th St & Oregon Ave point sat on Snyder
# Ave, 0.9 km off; OSM has that ShopRite on Oregon Ave at 24th. West Caldwell's point was a rounded
# guess 2.9 km from 900 Bloomfield Ave (OSM address point). Selden (71 College Plaza) and Patchogue
# (beside Sunrise Hwy) were right where the locator put them.
FIX: dict[str, tuple[float, float]] = {"420": (-75.18627, 39.91961), "180": (-74.3009832, 40.8520368),
                                       "808": (-73.047136, 40.866247), "811": (-73.006099, 40.781066)}


def km(a, b) -> float:
    return math.hypot((a[0] - b[0]) * 111.32 * math.cos(math.radians(a[1])), (a[1] - b[1]) * 110.57)


def number(addr: str) -> str:
    m = re.match(r"\s*([\d-]+)", addr or "")
    return m.group(1) if m else ""


def street_key(s: str) -> str:
    """'Fischer Blvd' / 'Fischer Boulevard' -> 'fischer'; 'Route 72' / 'NJ-72' -> '72'."""
    s = re.sub(r"\b(nj|ny|pa|ct|us|route|rte|rt|state|highway|hwy)\b[- ]*", "", s.lower())
    words = re.findall(r"[a-z0-9]+", s)
    return words[0] if words else ""


def main() -> None:
    feats = [f for f in json.load(open(CACHE / "atp" / "shoprite_us.geojson", encoding="utf-8"))["features"]
             if f["properties"].get("name") == "ShopRite" and "soon" not in (f["properties"].get("branch") or "").lower()]
    census = {r[0]: tuple(map(float, r[5].split(","))) for r in csv.reader(open(CACHE / "census_out.csv", encoding="utf-8"))
              if r[2] == "Match"}
    osm = []
    for e in json.load(open(CACHE / "osm.json", encoding="utf-8"))["elements"]:
        if e["tags"].get("shop") == "supermarket":
            c = e.get("center") or e
            osm.append(((c["lon"], c["lat"]), e["tags"].get("addr:housenumber", ""),
                        street_key(e["tags"].get("addr:street", ""))))

    def near(p, r):
        return any(km(o[0], p) < r for o in osm)

    stores, how, look = [], collections.Counter(), []
    for f in feats:
        p = f["properties"]
        ref, a = p["ref"], tuple(f["geometry"]["coordinates"])
        c = census.get(ref)
        addr = p.get("addr:street_address", "")
        n, street = number(addr), street_key(addr[len(number(addr)):])
        same = [o for o in osm if n and o[1] == n and o[2] and o[2] == street
                and (km(o[0], a) < 10 or (c and km(o[0], c) < 10))]
        if ref in FIX:
            pt, src = FIX[ref], "hand"
        elif same:
            pt, src = min(same, key=lambda o: km(o[0], a))[0], "osm address"
        elif near(a, 0.3):
            pt, src = a, "locator (osm agrees)"
        elif c and near(c, 0.3):
            pt, src = min(osm, key=lambda o: km(o[0], c))[0], "census (osm agrees)"
        elif c and km(a, c) < 0.5:
            pt, src = a, "locator (census agrees)"
        else:
            pt, src = a, "locator (unchecked)"
            look.append((ref, p.get("addr:street_address"), p.get("addr:city"), a, c))
        how[src] += 1
        city, state = p.get("addr:city", "").strip(), p.get("addr:state", "")
        branch = re.sub(r"\s*-\s*Now Open!?", "", p.get("branch") or city)
        stores.append(dict(name=f"ShopRite of {branch}", town=f"{city}, {state}", state=state, ref=ref,
                           lon=pt[0], lat=pt[1], moved_km=round(km(pt, a), 2)))
    stores.sort(key=lambda s: (s["state"], s["town"]))
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    by = collections.Counter(s["state"] for s in stores)
    print(f"{len(stores)} ShopRites {by.most_common()}; positions {dict(how)}")
    print("moved > 0.5 km from the locator:", [(s["name"], s["moved_km"]) for s in stores if s["moved_km"] > 0.5])
    for x in look:
        print("  look:", x)


if __name__ == "__main__":
    main()
