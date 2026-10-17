"""Every amusement park in North America, by one rule (Max, 2026-10-13):
a park counts if it has a roller coaster or real water slides.

From OSM (extract.sh): park areas (tourism=theme_park, leisure=water_park)
and rides (roller coasters, water slides). City pools with a slide or two
are kept out by:

  1. the ride must sit inside a mapped park area,
  2. water slides only: at least MIN_SLIDES slides, or a park of at least
     MIN_HA hectares,
  3. water slides only: no pool-ish names (Aquatic Center, YMCA, ...).

Fallback for parks whose rides nobody mapped (Galaxyland, Playland): a
named park area that matches a Wikidata amusement/theme/water park with a
Wikipedia article and no closing date counts. The OSM area keeps closed
parks out (Wikidata rarely marks them closed).

Writes cache/parks.json and prints what each rule removed.

    python experiments/amusement_parks/inventory.py
"""

from __future__ import annotations

import collections
import json
import math
import re
from pathlib import Path

from shapely.geometry import shape
from shapely.strtree import STRtree

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
REGIONS = ("us", "canada", "mexico", "puerto-rico")
MIN_SLIDES = 3
MIN_HA = 3.0
RESORT_HA = 300     # an outline this big holding 2+ named parks is a resort, not a park
POOLISH = re.compile(r"aquatic|\bpool\b|piscina|albercas?\b|ymca|\brec(reation)?\b|natatorium|swim|community cent"
                     r"|splash park|hot springs|outdoor (adventure center|experience)|\(closed\)", re.I)
# Checked by hand (2026-10-13): borderline water-slide parks that aren't amusement parks.
NOT_PARKS = {"Clark's Bears",       # bear show at Clark's Trading Post
             "Cherry Hill"}         # campground pool


def hectares(g) -> float:
    k = math.cos(math.radians(g.centroid.y))
    return g.area * k * 111_320 ** 2 / 10_000


def load(region: str):
    parks, coasters, slides = [], [], []
    for f in json.load(open(CACHE / f"osm_{region}.geojson", encoding="utf-8"))["features"]:
        p, gt = f["properties"], f["geometry"]["type"]
        if p.get("tourism") == "theme_park" or p.get("leisure") == "water_park":
            if gt in ("Polygon", "MultiPolygon"):
                g = shape(f["geometry"]).buffer(0)
                parks.append(dict(name=p.get("name", ""), kind="theme" if p.get("tourism") == "theme_park" else "water",
                                  geom=g, ha=hectares(g), region=region, wd=p.get("wikidata")))
            continue
        if p.get("attraction") == "roller_coaster" or p.get("roller_coaster") in ("track", "station"):
            coasters.append(shape(f["geometry"]).representative_point())
        elif p.get("attraction") == "water_slide":
            slides.append(shape(f["geometry"]))
    return parks, coasters, slides


WD_MATCH_M = 1500
# Wikidata types that mean rides. Not "theme park": Wikidata uses it for eco-parks
# and heritage villages (Xcaret, Xplor) as often as for ride parks.
RIDE_TYPES = {"amusement park", "water park", "indoor water park", "Legoland"}
NAME_NOISE = {"park", "parc", "parque", "the", "and", "water", "waterpark", "theme", "amusement",
              "acuatico", "acuático", "aquatique", "powered", "by"}


def wikidata() -> list[dict]:
    """Open parks with a Wikipedia article (cache/wd.json, one row per type)."""
    items: dict[str, dict] = {}
    for x in json.load(open(CACHE / "wd.json", encoding="utf-8"))["results"]["bindings"]:
        q = x["p"]["value"].rsplit("/", 1)[1]
        lon, lat = map(float, x["coord"]["value"][6:-1].split())
        d = items.setdefault(q, dict(q=q, name=x["pLabel"]["value"], lon=lon, lat=lat, closed=False,
                                     links=int(x.get("links", {}).get("value", 0)), ride_park=False))
        d["ride_park"] |= x["typeLabel"]["value"] in RIDE_TYPES
        d["closed"] |= "dissolved" in x or x.get("useLabel", {}).get("value") in ("permanently closed", "abandoned")
    return [d for d in items.values() if not d["closed"] and d["links"] > 0 and d["ride_park"]]


def words(name: str) -> set[str]:
    w = "".join(c if c.isalnum() else " " for c in name.lower()).split()
    return {x for x in w if len(x) > 2 and x not in NAME_NOISE}


def same_name(a: str, b: str) -> bool:
    wa, wb = words(a), words(b)
    return bool(wa and wb and (wa & wb))


def metres(a, b) -> float:
    k = math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * k, a[1] - b[1]) * 111_320


def main() -> None:
    parks, why = [], collections.Counter()
    for region in REGIONS:
        P, Cs, Ss = load(region)
        why["park areas mapped"] += len(P)
        ct, st = STRtree(Cs) if Cs else None, STRtree(Ss) if Ss else None
        for d in P:
            g = d.pop("geom")
            d["coaster"] = bool(ct is not None and len(ct.query(g, predicate="intersects")))
            # A slide drawn as several pieces counts once per mapped object; good enough for >= 3.
            d["slides"] = int(len(st.query(g, predicate="intersects"))) if st is not None else 0
            c = g.representative_point()
            d["lon"], d["lat"], d["_g"] = c.x, c.y, g
            parks.append(d)

    # A park drawn inside another (a water park within a theme park's gates)
    # is part of it: its rides join the bigger one.
    parks.sort(key=lambda d: -d["ha"])
    tree = STRtree([d["_g"] for d in parks])
    for i, d in enumerate(parks):
        if d.get("inside") is not None:
            continue
        for j in tree.query(d["_g"].representative_point(), predicate="intersects"):
            j = int(j)
            if j < i and parks[j].get("inside") is None and parks[j]["ha"] > d["ha"]:
                d["inside"] = j
                parks[j]["coaster"] |= d["coaster"]
                parks[j]["slides"] += d["slides"]
                break
    # Unless the outer one is a resort (Walt Disney World, Universal Orlando):
    # two or more named parks inside it are the parks, the resort isn't one.
    kids = collections.defaultdict(list)
    for d in parks:
        if d.get("inside") is not None and d["name"]:
            kids[d["inside"]].append(d)
    for j, ks in kids.items():
        if len(ks) >= 2 and parks[j]["ha"] >= RESORT_HA:
            print(f"  resort: {parks[j]['name']} -> {[d['name'] for d in ks]}")
            parks[j]["resort"] = True
            for d in ks:
                d["inside"] = None
    why["resort split into its parks"] = sum(1 for d in parks if d.get("resort"))
    top = [d for d in parks if d.get("inside") is None and not d.get("resort")]
    why["drawn inside another park"] = len(parks) - len(top) - why["resort split into its parks"]

    WD = wikidata()
    out = []
    for d in top:
        if not d["name"]:
            why["no name"] += 1
            continue
        if d["coaster"]:
            d["why"] = "coaster"
        elif d["slides"] == 0:
            wd = next((w for w in WD if w["q"] == d["wd"]), None) or min(
                (w for w in WD if metres((w["lon"], w["lat"]), (d["lon"], d["lat"])) < WD_MATCH_M
                 and same_name(w["name"], d["name"])),
                key=lambda w: metres((w["lon"], w["lat"]), (d["lon"], d["lat"])), default=None)
            if wd is None:
                why["no coaster or slide mapped"] += 1
                continue
            d["why"], d["wd"] = "wikipedia", wd["q"]
        elif d["slides"] < MIN_SLIDES and d["ha"] < MIN_HA:
            why[f"1-{MIN_SLIDES - 1} slides and under {MIN_HA:g} ha"] += 1
            continue
        elif POOLISH.search(d["name"]) or d["name"] in NOT_PARKS:
            why["pool-ish name"] += 1
            continue
        else:
            d["why"] = "slides"
        d["border"] = d["why"] == "slides" and (d["slides"] == MIN_SLIDES or d["ha"] < 2 * MIN_HA)
        out.append({k: v for k, v in d.items() if not k.startswith("_") and k != "inside"})

    json.dump(out, open(CACHE / "parks.json", "w", encoding="utf-8"), indent=0)
    for k, v in why.items():
        print(f"  {k}: {v}")
    print(len(out), "parks:", dict(collections.Counter(d["region"] for d in out)),
          dict(collections.Counter(d["why"] for d in out)), "borderline:", sum(d["border"] for d in out))


if __name__ == "__main__":
    main()
