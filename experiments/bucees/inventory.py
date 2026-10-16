"""Every Buc-ee's (Claude's pick, 2026-10-16: "surprise me"), one drive (Optimal Drives).

Data probe (2026-10-16): AllThePlaces bucees_us returned 0 features in the 2026-10-10 run, so:
- the list is Buc-ee's own locations page (buc-ees.com/locations, via firecrawl): 57 open stores in 14
  states, 36 in Texas, matching the San Marcos opening news (Aug 12, 2026: "57 locations, 36 in Texas").
  cache/official.json.
- positions: OpenStreetMap Buc-ee's features (osmium tags-filter brand:wikidata=Q4982335 / name /
  brand -> data/scratch/bucees.geojson; a store's shop and fuel features within 0.8 km are one: 56
  stores, cache/osm_stores.json), matched to each official store by its address (Census geocoder) or
  its town (the app's places index). 52 matched so; Fort Worth and Huber Heights match the two OSM
  stores just past the 25 km town radius; Benton, AR (not in OSM) is the Census geocode of its
  address; San Marcos (opened Aug 2026, not in OSM yet) is the Yarrington Road / I-35 interchange.
  Two OSM points not on the official list (central Tennessee, the Texas coast) are left out.

    python experiments/bucees/inventory.py

Writes cache/stores.json (name, town, state, lon, lat) for walmart/build.py --cache.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
FIX = {"Fort Worth, TX": (-97.278, 33.025), "Huber Heights, OH": (-84.054, 39.871),
       "San Marcos, TX": (-97.889, 29.937)}


def main() -> None:
    chk = json.load(open(CACHE / "stores_check.json", encoding="utf-8"))
    stores = []
    for c in chk:
        if c["town"] in FIX:
            c["lon"], c["lat"] = FIX[c["town"]]
        assert c["lon"] is not None, c["town"]
        stores.append(dict(name=f"Buc-ee's {c['town']}", town=c["town"], state=c["state"], lon=c["lon"], lat=c["lat"]))
    # names carry the state (Richmond, KY and Richmond, TX); three Angletons etc.: number the repeats
    seen = collections.Counter(s["town"] for s in stores)
    k = collections.Counter()
    for s in stores:
        if seen[s["town"]] > 1:
            k[s["town"]] += 1
            s["name"] = f"{s['name']} #{k[s['town']]}"
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    by = collections.Counter(s["state"] for s in stores)
    print(f"{len(stores)} Buc-ee's in {len(by)} states: {by.most_common()}")


if __name__ == "__main__":
    main()
