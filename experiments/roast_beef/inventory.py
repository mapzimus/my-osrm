"""Every roast beef shop in Massachusetts, one point each.

A roast beef shop (Max, 2026-10-14: "it doesn't have to have beef in the name"):
  - OpenStreetMap (extract.sh): a Massachusetts food place with "roast beef" in
    its name, or cuisine roast_beef / north_shore_roast_beef;
  - or any Massachusetts shop on Boston.com's readers' list, "71 of the best
    roast beef sandwich shops in Greater Boston and beyond" (2024-04-26),
    cache/bostoncom_2024.json, geocoded with the US Census geocoder
    (cached in cache/geocode.json): The Modern Butcher, Land 'N Sea, Cutty's...;
  - plus EXTRA chain locations both miss, minus CLOSED ones OSM still has.
Two entries are one shop if their names start with the same word and they are
within MATCH_M (Supreme and Giovanni's in Danvers are 130 m apart).

    sh experiments/roast_beef/extract.sh
    python experiments/roast_beef/inventory.py

Writes cache/stores.json (name, lon, lat, town, source).
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import shapefile
from shapely.geometry import shape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = HERE / "cache"
ROAST = re.compile(r"roast\s*beef", re.I)
MATCH_M = 400
# One storefront, two names: OSM's name -> current name. 459 MA-28, West Yarmouth: both
# listings share the address and reviews (Restaurantji); Boston.com 2024 calls it Mike's.
RENAMED = {"The One Roast Beef & Pizzeria": "Mike's Roast Beef"}
# Chain locations missing from both lists (web check 2026-10-14):
# Bill & Bob's, 57 Main St, Woburn (Foursquare, Hungry Onion); Brother's, Malden and Kingston
# (brothersroastbeef.com listings, Backyard Road Trips 2021).
EXTRA = [("Bill & Bob's Roast Beef", "57 Main St", "Woburn"),
         ("Brother's Roast Beef", "986 Eastern Ave", "Malden"),
         ("Brother's Roast Beef", "137 Main St", "Kingston")]
# In OSM but closed: Harrison's, North Andover (closed 2023: Boston.com, NBC Boston);
# Kelly's in Jordan's Furniture, Natick (closed 2018, NBC Boston).
CLOSED = [("Harrison's", -71.1243, 42.6801), ("Kelly's", -71.35, 42.30)]


def km(a, b) -> float:
    k = math.cos(math.radians(a[1]))
    return math.hypot((a[0] - b[0]) * k, a[1] - b[1]) * 111.2


def key(name: str) -> str:
    """First word, letters only: "King's Roast Beef" and "Kings Famous..." -> kings."""
    words = re.sub(r"[^a-z ]", "", name.lower().replace("’", "").replace("'", "")).split()
    words = [w for w in words if w != "the"]
    return words[0].rstrip("s") if words else ""                # Royal's = Royal


def same(a: dict, b: dict) -> bool:
    """Same shop: same first word, within MATCH_M. (Not distance alone: Supreme and The Modern
    Butcher are 49 m apart on Maple St, Danvers.)"""
    d = km((a["lon"], a["lat"]), (b["lon"], b["lat"])) * 1000
    return d < MATCH_M and key(a["name"]) == key(b["name"])


def massachusetts():
    sf = shapefile.Reader(str(ROOT / "data/naturalearth/ne_10m_admin_1_states_provinces"
                              / "ne_10m_admin_1_states_provinces.shp"))
    return next(shape(s.__geo_interface__) for s, r in zip(sf.shapes(), sf.records())
                if r["name"] == "Massachusetts").buffer(0.01)


def geocode(street: str, town: str, cache: dict) -> tuple[float, float] | None:
    street = street.replace(".", "").replace("Highway", "Hwy")
    address = f"{street}, {town}, MA"
    if address not in cache:
        q = urllib.parse.urlencode(dict(address=address, benchmark="Public_AR_Current", format="json"))
        for attempt in range(4):                              # the Census server times out now and then
            try:
                with urllib.request.urlopen(
                        f"https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?{q}", timeout=60) as r:
                    m = json.load(r)["result"]["addressMatches"]
                break
            except OSError:
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
        cache[address] = [m[0]["coordinates"]["x"], m[0]["coordinates"]["y"]] if m else nominatim(street, town)
        json.dump(cache, open(CACHE / "geocode.json", "w"), indent=0)
        time.sleep(0.3)
    return tuple(cache[address]) if cache[address] else None


def nominatim(street: str, town: str) -> list[float] | None:
    """OpenStreetMap's geocoder, for the addresses the Census one can't place (1 request/s policy)."""
    q = urllib.parse.urlencode(dict(street=street, city=town, state="MA", country="USA", format="json", limit=1))
    req = urllib.request.Request(f"https://nominatim.openstreetmap.org/search?{q}",
                                 headers={"User-Agent": "my-osrm roast beef video (mapzimus)"})
    time.sleep(1.1)
    with urllib.request.urlopen(req, timeout=60) as r:
        m = json.load(r)
    return [float(m[0]["lon"]), float(m[0]["lat"])] if m else None


def closed(name: str, lon: float, lat: float) -> bool:
    return any(key(name) == key(n) and km((lon, lat), (x, y)) < 15 for n, x, y in CLOSED)


def main() -> None:
    ma = massachusetts()
    stores, dropped = [], []
    for f in json.load(open(CACHE / "osm_ma_food.geojson", encoding="utf-8"))["features"]:
        p = f["properties"]
        if f["geometry"]["type"] == "LineString":                # osmium exports a building twice
            continue
        name = (p.get("name") or "").replace("’", "'")
        name = RENAMED.get(name, name)
        if not (ROAST.search(name) or "roast_beef" in (p.get("cuisine") or "")):
            continue
        pt = shape(f["geometry"]).representative_point()
        if not ma.contains(pt):
            continue
        s = dict(name=name, lon=round(pt.x, 6), lat=round(pt.y, 6), town=p.get("addr:city") or "",
                 source="OpenStreetMap")
        if closed(name, pt.x, pt.y):
            dropped.append(f"{name} (closed)")
        elif any(same(s, o) for o in stores):
            dropped.append(f"{name} (mapped twice)")
        else:
            stores.append(s)

    gpath = CACHE / "geocode.json"
    gcache = json.load(open(gpath)) if gpath.exists() else {}
    listed = [(e["name"], e["address"], e["town"], "Boston.com list")
              for e in json.load(open(CACHE / "bostoncom_2024.json", encoding="utf-8"))
              if e["town"] and ", " not in e["town"]]           # no chains without an address, no NH
    for name, street, town, source in listed + [(*e, "chain location") for e in EXTRA]:
        name = name.replace("’", "'").replace("‘", "'")
        ll = geocode(street, town, gcache)
        if ll is None:
            dropped.append(f"{name}, {town} (address not found)")
            continue
        s = dict(name=name, lon=round(ll[0], 6), lat=round(ll[1], 6), town=town, source=source)
        if not any(same(s, o) for o in stores):
            stores.append(s)
    json.dump(gcache, open(gpath, "w"), indent=0)
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    by = {}
    for s in stores:
        by[s["source"]] = by.get(s["source"], 0) + 1
    print(f"{len(stores)} roast beef shops in Massachusetts: {by}")
    print("dropped:", dropped)


if __name__ == "__main__":
    main()
