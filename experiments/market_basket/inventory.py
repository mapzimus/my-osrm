"""Every Market Basket (Max, 2026-10-17: "i want to do all market baskets").

Source: AllThePlaces market_basket_us (Market Basket's own store API; run in cache/atp/run.txt):
97 entries. Re-read live from shopmarketbasket.com/wp-json/mb/v1/store-locations the same day
(Max: "double check the number, they're always opening new stores"): the same 97. 7 are MB
Spirits / MB Beer & Wine shops and are left out; 90 Market Baskets are open (the chain says
"90 stores throughout New England"). #96 Scarborough, ME opens November 2026 (Mainebiz,
2026-09-22); Max: "put the scarborough store on anyways", so it is in and marked as opening.
Seekonk, MA is not on the locator yet: the old Showcase Cinemas on Route 6 (800 Fall River Ave)
was torn down for it, opening targeted for spring 2027 (WPRI, 2025-12-17). Max: "do seekonk only
with a note" (a Dartmouth rumour stays out).

Positions (the ShopRite rule): the locator point if an OSM Market Basket (Overpass,
brand:wikidata Q2079198, cache/osm.json) is within 0.3 km; otherwise settled by hand below
against OSM's address and the Census geocoder.

    python experiments/market_basket/inventory.py

Writes cache/stores.json (name, town, state, lon, lat) for walmart/build.py --cache.
"""

from __future__ import annotations

import collections
import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
SOON = {"96": "opening Nov 2026"}
# Planned, not on the locator: (name, town, state, lon, lat, note). Seekonk: Nominatim, 800 Fall River Ave.
PLANNED = [("Market Basket Seekonk", "Seekonk", "MA", -71.331584, 41.806084, "opening 2027")]
# Settled by hand (2026-10-17). OSM has the store with the same street address, the locator is
# 0.4-1.0 km off: Rindge (497 Route 202; Census agrees with OSM), Fall River (600 William S
# Canning Blvd), Westbrook (90 Rock Row; Census 4 km off), Salem NH (69 Central St), Lee (54 Calef Hwy).
FIX: dict[str, tuple[float, float]] = {"47": (-72.037846, 42.756822), "83": (-71.15601, 41.671547),
                                       "85": (-70.332878, 43.67606), "84": (-71.227516, 42.777012),
                                       "51": (-71.007136, 43.153566)}
# Locator point confirmed elsewhere: Census on the address (North Conway, not in OSM yet;
# Londonderry, where OSM's nearest point is another address), Nominatim on the store
# (Newburyport, West Bridgewater, South Attleboro: in OSM without the brand tag), and the
# Scarborough site at Market St and Scarborough Downs Rd (Mainebiz).
CHECKED = {"94", "42", "40", "70", "76", "96"}


def km(a, b) -> float:
    return math.hypot((a[0] - b[0]) * 111.32 * math.cos(math.radians(a[1])), (a[1] - b[1]) * 110.57)


def main() -> None:
    feats = json.load(open(CACHE / "atp" / "market_basket_us.geojson", encoding="utf-8"))["features"]
    osm = [((e.get("center") or e)["lon"], (e.get("center") or e)["lat"])
           for e in json.load(open(CACHE / "osm.json", encoding="utf-8"))["elements"]]
    stores, how, out = [], collections.Counter(), []
    for f in feats:
        p = f["properties"]
        m = re.match(r"Market Basket #(\d+)", p["name"])
        if not m:
            out.append(p["name"])
            continue
        ref, a = m.group(1), tuple(f["geometry"]["coordinates"])
        if "soon" in p["name"].lower() and ref not in SOON:
            out.append(p["name"])
            continue
        if ref in FIX:
            pt, src = FIX[ref], "hand (osm address)"
        elif any(km(o, a) < 0.3 for o in osm):
            pt, src = a, "locator (osm agrees)"
        elif ref in CHECKED:
            pt, src = a, "locator (checked)"
        else:
            raise SystemExit(f"#{ref} {p['addr:city']}: no source agrees with {a}; settle it by hand")
        how[src] += 1
        city, state = p["addr:city"].strip(), p["addr:state"]
        stores.append(dict(name=f"Market Basket #{ref}", town=f"{city}, {state}", state=state, ref=ref,
                           lon=pt[0], lat=pt[1], note=SOON.get(ref, "")))
    for name, city, state, lon, lat, note in PLANNED:
        how["planned (news)"] += 1
        stores.append(dict(name=name, town=f"{city}, {state}", state=state, ref=city.lower(), lon=lon, lat=lat, note=note))
    stores.sort(key=lambda s: int(s["ref"]) if s["ref"].isdigit() else 999)
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    by = collections.Counter(s["state"] for s in stores)
    print(f"{len(stores)} Market Baskets {by.most_common()}; positions {dict(how)}")
    print("left out:", out)


if __name__ == "__main__":
    main()
