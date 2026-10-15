"""The smallest town in every state (ideas #297 + #296), one drive (Optimal Drives).

Max (2026-10-15): all 50 states, a combined population at the end, no 0s.
Population: Census 2020 (the estimates base, ESTIMATESBASE2020, in the Census Bureau's
city and town file cache/sub-est2024.csv; the 2024 estimate is kept for reference).

Data probe (2026-10-15) found and fixed:
- Towns: incorporated places (SUMLEV 162). In New England the towns are county
  subdivisions instead (SUMLEV 061), so those six states take the smaller of an active
  (FUNCSTAT A) town/plantation and an incorporated place (Fenwick, CT, 43, a borough).
- Hawaii has no incorporated places: its smallest Census-designated place, from the State
  of Hawaii GIS office's 2020 CDP layer (cache/hi_cdp.json): Manele, Lanai, 7.
- D.C. is not a state; 0-population places are out. Ties (Gross and Monowi, NE, 2; Cooperton
  and Lotsee, OK, 4): Max (2026-10-15) trimmed to 50, keeping Monowi (the famous one) and Lotsee.
Coordinates: Census 2020 gazetteer (places, county subdivisions); Manele from its polygon.

    python experiments/smallest_towns/inventory.py

Writes cache/stores.json (name, town, state, pop, lon, lat) for walmart/build.py --cache.
"""

from __future__ import annotations

import collections
import csv
import json
import urllib.request
from pathlib import Path

import shapely
from shapely.geometry import shape

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
OSRM = "http://127.0.0.1:5002"
NEW_ENGLAND = {"Connecticut", "Maine", "Massachusetts", "New Hampshire", "Rhode Island", "Vermont"}
ABBR = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
        "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
        "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
        "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
        "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
        "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
        "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
        "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
        "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
        "Wisconsin": "WI", "Wyoming": "WY"}
# Gazetteer points that miss where people live (checked 2026-10-15): Gosnold's point lands among its
# uninhabited Elizabeth Islands; its people are in Cuttyhunk village.
# Cuttyhunk has no road link in the routing data, so the drive ends at the Cuttyhunk ferry dock in
# New Bedford and boats.py adds the boat round trip (otherwise build.py moves the stop to the nearest
# road it can reach: Aquinnah on Martha's Vineyard, 10 km across the water).
FIX = {("Gosnold", "MA"): (-70.9197, 41.6338)}
TIE_DROP = {("Gross", "NE"), ("Cooperton", "OK")}
SUFFIX = (" city", " town", " village", " borough", " plantation", " CDP")


def short(name: str) -> str:
    for s in SUFFIX:
        if name.endswith(s):
            return name[: -len(s)]
    return name


def gazetteer(fn: str) -> dict[str, tuple[float, float]]:
    out = {}
    with open(CACHE / fn, encoding="utf-8") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            r = {k.strip(): v.strip() for k, v in r.items()}
            out[r["GEOID"]] = (float(r["INTPTLONG"]), float(r["INTPTLAT"]))
    return out


def main() -> None:
    rows = list(csv.DictReader(open(CACHE / "sub-est2024.csv", encoding="latin-1")))
    places = gazetteer("2020_Gaz_place_national.txt")
    cousubs = gazetteer("2020_Gaz_cousubs_national.txt")
    cand = collections.defaultdict(list)
    for r in rows:
        st, pop = r["STNAME"], int(r["ESTIMATESBASE2020"])
        if st not in ABBR or st == "Hawaii" or pop <= 0:
            continue
        if r["SUMLEV"] == "162":
            ll = places.get(r["STATE"] + r["PLACE"])
        elif r["SUMLEV"] == "061" and st in NEW_ENGLAND and r["FUNCSTAT"] == "A":
            ll = cousubs.get(r["STATE"] + r["COUNTY"] + r["COUSUB"])
        else:
            continue
        if ll:
            cand[st].append(dict(name=short(r["NAME"]), kind=r["NAME"].rsplit(" ", 1)[-1], pop=pop,
                                 pop2024=int(r["POPESTIMATE2024"]), lon=ll[0], lat=ll[1]))
    hi = [f for f in json.load(open(CACHE / "hi_cdp.json", encoding="utf-8"))["features"] if f["properties"]["pop20"]]
    m = min(hi, key=lambda f: f["properties"]["pop20"])
    pt = shapely.Point(shape(m["geometry"]).representative_point())
    cand["Hawaii"].append(dict(name=m["properties"]["name20"], kind="CDP", pop=m["properties"]["pop20"], pop2024=None,
                               lon=pt.x, lat=pt.y))
    stores = []
    for st in sorted(cand):
        low = min(c["pop"] for c in cand[st])
        for c in cand[st]:
            if c["pop"] == low and (c["name"], ABBR[st]) not in TIE_DROP:
                c["lon"], c["lat"] = FIX.get((c["name"], ABBR[st]), (c["lon"], c["lat"]))
                try:
                    with urllib.request.urlopen(f"{OSRM}/nearest/v1/driving/{c['lon']:.6f},{c['lat']:.6f}",
                                                timeout=30) as r:
                        snap = json.load(r)["waypoints"][0]["distance"]
                except OSError:
                    snap = -1
                flag = "  <-- check" if snap > 3000 or snap < 0 else ""
                print(f"  {ABBR[st]}  {c['name']:<22} {c['kind']:<10} pop {c['pop']:>5}  snap {snap:7.0f} m{flag}")
                stores.append(dict(name=c["name"], town=f"{c['name']}, {ABBR[st]}", state=ABBR[st], pop=c["pop"],
                                   kind=c["kind"], lon=c["lon"], lat=c["lat"]))
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0)
    print(f"{len(stores)} towns in {len({s['state'] for s in stores})} states; combined population "
          f"{sum(s['pop'] for s in stores):,}")


if __name__ == "__main__":
    main()
