"""Every town Route 66 passes through, Chicago to Santa Monica, with 2020 population (Max,
2026-10-16; same rules as i90_towns/inventory.py, which this copies).

"Passes through" = the Route 66 line (line.py: OSM Historic US 66, 1937-1985, with New Mexico's
1926-1937 Santa Fe loop) crosses the town's Census boundary or runs within BORDER_M of it.
None of the eight states is all-township country, so the towns are Census places: cities,
towns, villages and the unincorporated communities the Census maps (CDPs).

    python experiments/route66_towns/inventory.py

Writes cache/towns.json.
"""

from __future__ import annotations

import collections
import json
import math
import urllib.request
from pathlib import Path

import numpy as np
import shapefile
from shapely.geometry import LineString, Point, shape
from shapely.ops import nearest_points
from shapely.strtree import STRtree

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
CENSUS = HERE.parent / "i95_towns" / "cache"      # boundary files + estimates, shared
MCD_STATES: set[str] = set()                        # Route 66's states: Census places everywhere
# NYC's boroughs aren't split out in the estimates file: 2020 Census counts.
BOROUGHS = {"Bronx borough": 1_472_654, "Manhattan borough": 1_694_251, "Brooklyn borough": 2_736_074,
            "Queens borough": 2_405_464, "Staten Island borough": 495_747}
# Border towns count (Max 2026-10-14: "include the border towns like Santee"):
# I-90 crosses the town or runs within BORDER_M of its line, about the error
# of the 1:500k cartographic boundaries.
BORDER_M = 100
NOT_TOWNS = ("County subdivisions not defined", "Unorganized Territory", "Reservation")      # Penobscot Indian Island Reservation too


def route66() -> LineString:
    return LineString(json.load(open(CACHE / "line.json"))["line"])      # Chicago to Santa Monica


def population() -> dict[str, int]:
    """GEOID -> 2020 population (Census estimates base, sub-est2023.csv: every
    incorporated place and county subdivision; the Census API now needs a key)."""
    import csv
    out = {}
    for r in csv.DictReader(open(CENSUS / "sub-est2023.csv", encoding="latin-1")):
        if r["SUMLEV"] == "162":
            out[r["STATE"] + r["PLACE"]] = int(r["ESTIMATESBASE2020"] or 0)
        elif r["SUMLEV"] == "061":
            out[r["STATE"] + r["COUNTY"] + r["COUSUB"]] = int(r["ESTIMATESBASE2020"] or 0)
    return out


def osm_population(t: dict, db) -> int:
    """Unincorporated communities (CDPs) aren't in the estimates file: the
    map app's places index (OSM), same name within 10 km."""
    lon, lat = t["centroid"]
    r = db.execute("select population from places where country='US' and name=? and abs(lon-?)<0.12 "
                   "and abs(lat-?)<0.09 order by population desc limit 1", (t["name"], lon, lat)).fetchone()
    return int(r[0]) if r and r[0] else 0


def wikidata_population(gnis: list[str]) -> dict[str, int]:
    """Latest population on Wikidata for GNIS ids (P590)."""
    import urllib.parse
    if not gnis:
        return {}
    vals = " ".join(f'"{int(g)}"' for g in gnis)
    q = f"""SELECT ?g (MAX(?p) AS ?pop) WHERE {{ VALUES ?g {{ {vals} }} ?i wdt:P590 ?g; wdt:P1082 ?p. }} GROUP BY ?g"""
    req = urllib.request.Request("https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q),
                                 headers={"User-Agent": "my-osrm route66_towns (mapzimus)"})
    rows = json.load(urllib.request.urlopen(req, timeout=120))["results"]["bindings"]
    return {r["g"]["value"]: int(float(r["pop"]["value"])) for r in rows}


def main() -> None:
    road = route66()
    lon0, lat0, lon1, lat1 = road.bounds
    found = []
    for kind, keep in (("cousub", lambda st: st in MCD_STATES), ("place", lambda st: st not in MCD_STATES)):
        sf = shapefile.Reader(str(CENSUS / kind / f"cb_2023_us_{kind}_500k.shp"))
        for sh, rec in zip(sf.iterShapes(), sf.iterRecords()):
            b = sh.bbox
            if b[2] < lon0 or b[0] > lon1 or b[3] < lat0 or b[1] > lat1 or not keep(rec["STATEFP"]):
                continue
            if any(w in rec["NAMELSAD"] for w in NOT_TOWNS) or rec["NAMELSAD"].endswith(" UT"):   # Maine's unorganized territories: no town
                continue
            g = shape(sh.__geo_interface__)
            m_per_deg = 111_200 * math.cos(math.radians(g.centroid.y))
            if g.distance(road) * m_per_deg > BORDER_M:
                continue
            cut = g.intersection(road)
            if cut.length * m_per_deg < 1:          # runs along the border: the stretch of road beside it
                cut = road.intersection(g.buffer(BORDER_M / m_per_deg))
                if cut.is_empty:
                    cut = nearest_points(g, road)[1]
            found.append(dict(kind=kind, geoid=rec["GEOID"], name=rec["NAME"], full=rec["NAMELSAD"],
                              state=rec["STUSPS"], st=rec["STATEFP"], gnis=rec[f"{kind.upper()}NS"]
                              if f"{kind.upper()}NS" in rec.as_dict() else rec.as_dict().get("PLACENS", ""),
                              at=road.project(cut.representative_point()) if not cut.is_empty else 0.0,
                              miles=cut.length * 0, centroid=list(g.representative_point().coords[0])))
    states = {t["st"] for t in found}
    import sqlite3
    pops, db = population(), sqlite3.connect(HERE.parents[1] / "data" / "app" / "places.sqlite")
    for t in found:
        t["pop"] = BOROUGHS.get(t["full"]) or pops.get(t["geoid"]) or osm_population(t, db)
        t["pop_src"] = "census" if t["geoid"] in pops or t["full"] in BOROUGHS else "osm"
    wd = wikidata_population([t["gnis"] for t in found if not t["pop"] and t["gnis"]])
    for t in found:
        if not t["pop"] and t["gnis"] and str(int(t["gnis"])) in wd:
            t["pop"], t["pop_src"] = wd[str(int(t["gnis"]))], "wikidata"
        t.pop("miles")
    found.sort(key=lambda t: t["at"])
    # Same name twice in a state (Rye town / Rye city): say which.
    seen = collections.Counter((t["name"], t["state"]) for t in found)
    for t in found:
        kind = t["full"][len(t["name"]):].strip().replace("township", "twp")
        t["label"] = f"{t['name']} ({kind})" if seen[t["name"], t["state"]] > 1 and kind else t["name"]
    # Point on I-90 where the drive is "in" each town (for timing and labels).
    for t in found:
        t["road"] = list(road.interpolate(t["at"]).coords[0])
    json.dump(found, open(CACHE / "towns.json", "w"), indent=0)
    pop = np.array([t["pop"] for t in found])
    print(f"{len(found)} towns in {len(states)} states, population 0: {int((pop == 0).sum())}, "
          f"median {int(np.median(pop)):,}, total {int(pop.sum()):,}")
    for t in sorted(found, key=lambda t: -t["pop"])[:12]:
        print(f"  {t['full']}, {t['state']}: {t['pop']:,}")
    print("first:", [t["name"] for t in found[:5]], "last:", [t["name"] for t in found[-5:]])


if __name__ == "__main__":
    main()
