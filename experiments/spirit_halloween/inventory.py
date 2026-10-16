"""Every Spirit Halloween of the 2026 season (Max, 2026-10-16), one trip (Optimal Drives).

Source: AllThePlaces spirit_halloween (Spirit's own store locator), run 2026-10-10, mid-season
(cache/atp/run.txt). Data probe (2026-10-16): 1,586 stores, 1,497 in the US, no duplicate refs,
all with coordinates and this season's hours; every state has one (AK 4, HI 4; DC none), in
line with Spirit's "over 1,500 stores". Alaska's four are on the road network; Hawaii's (3 Oahu,
1 Maui) are flown to by walmart/build.py. Canada is out.

    python experiments/spirit_halloween/inventory.py

Writes cache/stores.json (name, town, state, lon, lat) for walmart/build.py --cache.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"


def main() -> None:
    feats = json.load(open(CACHE / "atp" / "spirit_halloween.geojson", encoding="utf-8"))["features"]
    stores = []
    for f in feats:
        p = f["properties"]
        if p.get("addr:country") != "US":
            continue
        city, state = p.get("addr:city", "").strip(), p.get("addr:state", "")
        lon, lat = f["geometry"]["coordinates"]
        ref = p["ref"].rstrip("/").rsplit("-", 1)[-1].removesuffix(".html")      # .../halloween-store-68508.html
        stores.append(dict(name=f"Spirit Halloween #{ref}", town=f"{city}, {state}", state=state, ref=ref,
                           lon=lon, lat=lat))
    stores.sort(key=lambda s: (s["state"], s["town"]))
    assert len({s["ref"] for s in stores}) == len(stores)
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    by = collections.Counter(s["state"] for s in stores)
    print(f"{len(stores)} Spirit Halloweens in {len(by)} states; top: {by.most_common(6)}")


if __name__ == "__main__":
    main()
