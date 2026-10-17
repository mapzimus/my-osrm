"""Every Chick-fil-A (viewer request, parkermanh on TikTok, 2026-10-17: "Do every chick fil a").

Source: AllThePlaces chick_fil_a (Chick-fil-A's own locator; run in cache/atp/run.txt): 3,475
entries, no spider errors; ScrapeHero counted 3,480 US (Sept 2026). Chick-fil-A: "more than 3,000
restaurants in 48 states, Washington, D.C., Puerto Rico and Canada" (none in Alaska or Vermont).
Scope (Max, 2026-10-17): every state with one, DC, Puerto Rico and Canada; Hawaii and Puerto Rico
by plane (walmart/build.py's flights).
Left out:
- not restaurants: "Chick-fil-A Delivery" kitchens, test locations, "Little Blue Menu";
- closed (end_date) or not open yet (start_date after TODAY);
- inside airports (Max: past security there's no driving to it); "Pre Security" and the
  "Airport Blvd/Highway/Road/Way/Drive" street names stay.

    python experiments/chickfila/inventory.py

Writes cache/stores.json (name, town, state, lon, lat) for walmart/build.py --cache.
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
TODAY = "2026-10-17"
NOT_RESTAURANT = re.compile(r"delivery|test location|little blue menu", re.I)
AIRPORT = re.compile(r"airport|concourse|terminal", re.I)
AIRPORT_OK = re.compile(r"pre security|airport (highway|hwy|blvd|road|rd|way|drive|dr)\b", re.I)


def main() -> None:
    feats = json.load(open(CACHE / "atp" / "chick_fil_a.geojson", encoding="utf-8"))["features"]
    stores, out = [], collections.Counter()
    airports = []
    for f in feats:
        p = f["properties"]
        name, branch = p.get("name", ""), p.get("branch") or ""
        if NOT_RESTAURANT.search(name) or NOT_RESTAURANT.search(branch):
            out["not a restaurant"] += 1
            continue
        if p.get("end_date"):
            out["closed"] += 1
            continue
        if (p.get("start_date") or "") > TODAY:
            out["not open yet"] += 1
            continue
        if AIRPORT.search(branch) and not AIRPORT_OK.search(branch):
            out["inside an airport"] += 1
            airports.append(branch)
            continue
        lon, lat = f["geometry"]["coordinates"]
        country, state = p.get("addr:country"), p.get("addr:state", "")
        city = p.get("addr:city", "").strip()
        s = dict(name=f"Chick-fil-A {branch or city} {p['ref']}", town=f"{city}, {state}", state=state,
                 country=country, ref=p["ref"], lon=lon, lat=lat)
        if country == "PR" or state == "PR":
            s["region"] = "puerto-rico"                     # walmart/build.py: flights to the island
        stores.append(s)
    stores.sort(key=lambda s: (s["country"], s["state"], s["town"]))
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    by = collections.Counter(s["state"] for s in stores)
    ctry = collections.Counter(s["country"] for s in stores)
    print(f"{len(stores)} Chick-fil-As {dict(ctry)}, {len(by)} states/provinces; left out {dict(out)}")
    print("airports left out:", airports)


if __name__ == "__main__":
    main()
