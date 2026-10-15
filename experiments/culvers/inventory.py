"""Every Culver's: a viewer request (Liem Johnson, 2026-10-15: "Can you do every Culvers
please, I followed you"). The comment opens the video, like nba/.

Source: AllThePlaces culvers_us (Culver's own store locator API; run in
cache/atp/run.txt). Data probe (2026-10-15): 1,114 entries, no duplicate refs.
Kept: stores open today (start_date <= TODAY; 34 open Nov 2026 - Feb 2027 are out).
Dropped: the lone "Alaska" store at Utqiagvik, which has no road to anywhere, a bad
record (Culver's says 26 states; with it the data has 27). Result: 1,080 in 26 states,
in line with Culver's "1,000 restaurants" (2026 release) and xmap's 1,040 (July 2026).

    curl -sfL -o cache/atp/culvers_us.geojson https://alltheplaces-data.openaddresses.io/runs/<run>/output/culvers_us.geojson
    python experiments/culvers/inventory.py

Writes cache/stores.json (name, town, state, lon, lat) for walmart/build.py --cache.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
TODAY = "2026-10-15"
BAD = {("Utqiagvik", "AK")}


def main() -> None:
    feats = json.load(open(CACHE / "atp" / "culvers_us.geojson", encoding="utf-8"))["features"]
    stores, future = [], 0
    for f in feats:
        p = f["properties"]
        city, state = p.get("addr:city", "").strip(), p.get("addr:state", "")
        if (p.get("start_date") or "") > TODAY:
            future += 1
            continue
        if (city, state) in BAD:
            continue
        lon, lat = f["geometry"]["coordinates"]
        stores.append(dict(name=f"Culver's of {city}", town=f"{city}, {state}", state=state, ref=p.get("ref"),
                           lon=lon, lat=lat))
    stores.sort(key=lambda s: (s["state"], s["town"]))
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    by = collections.Counter(s["state"] for s in stores)
    print(f"{len(stores)} open Culver's in {len(by)} states ({future} not open yet); top: {by.most_common(6)}")


if __name__ == "__main__":
    main()
