"""Every SUBJECT: the list of stops. TEMPLATE: copy with experiments/new_video.py <slug>.

Source: AllThePlaces spider `SPIDER` (the chain's own locator), run into cache/atp/. Say here
what the count was, what the chain itself claims, and what is left out and why (format doc 11:
define what counts, apply it the same way everywhere, say what the number leaves out).

    python experiments/SLUG/inventory.py

Writes cache/stores.json (name, town, state, lon, lat; "region" for islands) for
experiments/walmart/build.py --cache experiments/SLUG/cache.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
TODAY = "YYYY-MM-DD"


def main() -> None:
    feats = json.load(open(CACHE / "atp" / "SPIDER.geojson", encoding="utf-8"))["features"]
    stores, out = [], collections.Counter()
    for f in feats:
        p = f["properties"]
        if p.get("end_date"):
            out["closed"] += 1
            continue
        if (p.get("start_date") or "") > TODAY:
            out["not open yet"] += 1
            continue
        lon, lat = f["geometry"]["coordinates"]
        state, city = p.get("addr:state", ""), p.get("addr:city", "").strip()
        s = dict(name=f"SUBJECT {p.get('branch') or city} {p['ref']}", town=f"{city}, {state}", state=state,
                 country=p.get("addr:country"), ref=p["ref"], lon=lon, lat=lat)
        if state in ("HI", "PR"):
            s["region"] = "hawaii" if state == "HI" else "puerto-rico"     # build.py: flights
        stores.append(s)
    stores.sort(key=lambda s: (s["country"] or "", s["state"], s["town"]))
    CACHE.mkdir(exist_ok=True)
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    by = collections.Counter(s["state"] for s in stores)
    print(f"{len(stores)} SUBJECTS in {len(by)} states; left out {dict(out)}")


if __name__ == "__main__":
    main()
