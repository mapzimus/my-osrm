"""Every A&W (viewer request, RADZZ_4357 on TikTok, 2026-10-17: "Do a map of every A&W in America").

Source: AllThePlaces aw_restaurants (A&W's own store locator API; run in cache/atp/run.txt).
Data probe (2026-10-17): 386 stores in 38 states, all in the US, no duplicate refs, no spider
errors, in line with ScrapeHero's 401 (March 2026) as the chain keeps shrinking. Both Alaska
stores (Wasilla, Fairbanks) are kept: the engine reaches them through Canada (Max: "keep Alaska").

    curl -sfL -o cache/atp/aw_restaurants.geojson https://alltheplaces-data.openaddresses.io/runs/<run>/output/aw_restaurants.geojson
    python experiments/aw/inventory.py

Writes cache/stores.json (name, town, state, lon, lat) for walmart/build.py --cache.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"


def main() -> None:
    feats = json.load(open(CACHE / "atp" / "aw_restaurants.geojson", encoding="utf-8"))["features"]
    stores = []
    for f in feats:
        p = f["properties"]
        city, state = p.get("addr:city", "").strip(), p.get("addr:state", "")
        lon, lat = f["geometry"]["coordinates"]
        stores.append(dict(name=f"A&W {city}, {state} {p['ref']}", town=f"{city}, {state}", state=state,
                           ref=p["ref"], drive_in=p.get("drive_in") == "yes", lon=lon, lat=lat))
    stores.sort(key=lambda s: (s["state"], s["town"]))
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    by = collections.Counter(s["state"] for s in stores)
    print(f"{len(stores)} A&Ws in {len(by)} states; top: {by.most_common(6)}")


if __name__ == "__main__":
    main()
