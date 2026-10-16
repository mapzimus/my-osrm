"""Every White Castle (Max, 2026-10-16), one drive (Optimal Drives).

Source: AllThePlaces white_castle (White Castle's own store locator; run in cache/atp/run.txt).
Data probe (2026-10-16): 336 entries, no duplicate refs, all with coordinates, in 15 states,
in line with White Castle's "about 340 restaurants". Spot-checked the far ones: 4 around
Orlando, 3 Nashville/Clarksville, 3 Phoenix, 3 Las Vegas (incl. the Jean travel stop), Kenosha
WI, Whitehall PA. The 7-digit refs are the newer and partner sites (casinos, Times Square);
they are on the locator, so they stay.

    curl -sfL -o cache/atp/white_castle.geojson https://alltheplaces-data.openaddresses.io/runs/<run>/output/white_castle.geojson
    python experiments/white_castle/inventory.py

Writes cache/stores.json (name, town, state, lon, lat) for walmart/build.py --cache.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"


def main() -> None:
    feats = json.load(open(CACHE / "atp" / "white_castle.geojson", encoding="utf-8"))["features"]
    stores = []
    for f in feats:
        p = f["properties"]
        city, state = p.get("addr:city", "").strip().title(), p.get("addr:state", "")
        lon, lat = f["geometry"]["coordinates"]
        stores.append(dict(name=f"White Castle #{p['ref']}", town=f"{city}, {state}", state=state, ref=p["ref"],
                           lon=lon, lat=lat))
    stores.sort(key=lambda s: (s["state"], s["town"]))
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    by = collections.Counter(s["state"] for s in stores)
    print(f"{len(stores)} White Castles in {len(by)} states; top: {by.most_common(6)}")


if __name__ == "__main__":
    main()
