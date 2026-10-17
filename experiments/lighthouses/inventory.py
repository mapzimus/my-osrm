"""One list of US lighthouses from OpenStreetMap + Wikidata, deduplicated,
with each one tagged: land or water, distance to the engine's road network,
historic or not. Read-only data check; writes cache/inventory.json.

    python experiments/lighthouses/inventory.py
"""

from __future__ import annotations

import collections
import json
import math
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from shapely.geometry import shape

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
sys.path.insert(0, str(HERE))
import landcheck  # noqa: E402

OSRM = "http://127.0.0.1:5002"
MERGE_M = 250          # two records closer than this are the same lighthouse


def metres(a: dict, b: dict) -> float:
    k = math.cos(math.radians((a["lat"] + b["lat"]) / 2))
    return math.hypot((a["lon"] - b["lon"]) * k, a["lat"] - b["lat"]) * 111_320


def osm() -> list[dict]:
    out = []
    for f in ("us", "puerto-rico"):
        for ft in json.load(open(CACHE / f"osm_{f}.geojson", encoding="utf-8"))["features"]:
            p = ft["properties"]
            if p.get("man_made") != "lighthouse" or ft["geometry"]["type"] == "LineString":
                continue           # outlines are exported again as areas
            if p.get("ruins") or p.get("disused") or p.get("abandoned"):
                continue
            c = shape(ft["geometry"]).centroid
            out.append(dict(src="osm", name=p.get("name", ""), lon=c.x, lat=c.y,
                            wd=p.get("wikidata"), historic=bool(p.get("heritage") or p.get("historic")
                                                                or p.get("ref:nrhp"))))
    return out


def wikidata() -> list[dict]:
    items = {}
    for x in json.load(open(CACHE / "wd.json", encoding="utf-8"))["results"]["bindings"]:
        q = x["l"]["value"].rsplit("/", 1)[1]
        lon, lat = map(float, x["coord"]["value"][6:-1].split())
        d = items.setdefault(q, dict(src="wd", name=x["lLabel"]["value"], lon=lon, lat=lat, wd=q,
                                     historic=False, st=None))
        d["historic"] |= "nrhp" in x
        if "stateLabel" in x:
            d["st"] = x["stateLabel"]["value"]
    return list(items.values())


def merge(recs: list[dict]) -> list[dict]:
    """OSM first (better coordinates); a Wikidata record joins an OSM one
    with the same Q id or within MERGE_M."""
    out, by_q = [], {}
    for r in sorted(recs, key=lambda r: r["src"] != "osm"):
        hit = by_q.get(r["wd"]) if r["wd"] else None
        if hit is None:
            hit = next((o for o in out if metres(o, r) < MERGE_M), None)
        if hit:
            hit["historic"] |= r["historic"]
            hit["name"] = hit["name"] or r["name"]
            hit["st"] = hit.get("st") or r.get("st")
            hit["srcs"].add(r["src"])
        else:
            hit = dict(r, srcs={r["src"]})
            out.append(hit)
        if r["wd"]:
            by_q[r["wd"]] = hit
    return out


NAME_NOISE = {"light", "lighthouse", "lights", "station", "head", "the", "of", "range",
              "front", "rear", "historic", "old", "new", "lt", "lh"}
NAME_MERGE_M = 3000    # same core name and this close: one lighthouse


def core(name: str) -> frozenset:
    words = "".join(c if c.isalnum() else " " for c in name.lower()).split()
    return frozenset(w for w in words if w not in NAME_NOISE)


def merge_names(L: list[dict]) -> list[dict]:
    """Second pass: records whose core names match (one contains the other)
    within NAME_MERGE_M. Front/rear range lights keep both ('front'/'rear'
    are dropped from the core, so they're kept apart by checking the raw words)."""
    out: list[dict] = []
    for d in L:
        cd, raw = core(d["name"]), set(d["name"].lower().split())
        hit = None
        for o in out:
            co = core(o["name"])
            if (cd and co and (cd <= co or co <= cd) and metres(o, d) < NAME_MERGE_M
                    and ({"front", "rear"} & raw) == ({"front", "rear"} & set(o["name"].lower().split()))):
                hit = o
                break
        if hit:
            hit["historic"] |= d["historic"]
            hit["srcs"] |= d["srcs"]
            hit["st"] = hit.get("st") or d.get("st")
        else:
            out.append(d)
    return out


def us_only(L: list[dict]) -> list[dict]:
    """Drop points inside Canada or Mexico; points in the sea go to the
    nearest country."""
    import shapefile
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    sf = shapefile.Reader(str(HERE.parents[1] / "data/naturalearth/ne_10m_admin_0_countries/ne_10m_admin_0_countries.shp"))
    geoms, codes = [], []
    for s, r in zip(sf.shapes(), sf.records()):
        if r["ADM0_A3"] in ("USA", "CAN", "MEX", "BHS", "CUB"):
            geoms.append(shape(s.__geo_interface__))
            codes.append(r["ADM0_A3"])
    tree = STRtree(geoms)
    return [d for d in L if codes[tree.nearest(Point(d["lon"], d["lat"]))] == "USA"]


REPLICA_WORDS = ("replica", "miniature", "mini golf", "golf")
# Lake Havasu City, AZ: ~25 scale replicas of famous lighthouses.
HAVASU = (-114.6, 34.2, -114.0, 34.7)


def real(d: dict) -> bool:
    n = d["name"].lower()
    if any(w in n for w in REPLICA_WORDS):
        return False
    x0, y0, x1, y1 = HAVASU
    return not (x0 < d["lon"] < x1 and y0 < d["lat"] < y1)


LIGHT_WORDS = ("light", "fyr", "faro")
INLAND = {"Arizona", "Arkansas", "Colorado", "Idaho", "Iowa", "Kansas", "Kentucky", "Missouri",
          "Montana", "Nebraska", "Nevada", "New Mexico", "North Dakota", "Oklahoma", "South Dakota",
          "Tennessee", "Utah", "West Virginia", "Wyoming"}


def lighthouse(d: dict, state: str) -> bool:
    """OSM alone isn't enough for a name without 'light' (airport beacons,
    marina towers) or for a lighthouse in a state with no coast or Great
    Lake (decorations on reservoirs). Wikidata-listed ones always count."""
    if "wd" in d["srcs"]:
        return True
    return any(w in d["name"].lower() for w in LIGHT_WORDS) and state not in INLAND


def states(L: list[dict]) -> None:
    import shapefile
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    sf = shapefile.Reader(str(CACHE / "states" / "ne_10m_admin_1_states_provinces.shp"))
    geoms, names = [], []
    for s, r in zip(sf.shapes(), sf.records()):
        if r["adm0_a3"] == "USA":
            geoms.append(shape(s.__geo_interface__))
            names.append(r["name"])
    tree = STRtree(geoms)
    for d in L:
        d["state"] = names[tree.nearest(Point(d["lon"], d["lat"]))]


def snap(d: dict) -> dict:
    with urllib.request.urlopen(f"{OSRM}/nearest/v1/driving/{d['lon']},{d['lat']}", timeout=30) as r:
        w = json.load(r)["waypoints"][0]
    d["snap_m"], d["road"] = w["distance"], w["location"]
    return d


def main() -> None:
    L = merge(osm() + wikidata())
    n0 = len(L)
    L = [d for d in L if -180 < d["lon"] < -60 and d["lat"] > 15]     # drop Pacific territories
    L = merge_names(L)
    n1 = len(L)
    L = us_only(L)
    n2 = len(L)
    L = [d for d in L if real(d)]
    n3 = len(L)
    states(L)
    L = [d for d in L if not d["name"] or lighthouse(d, d["state"])]
    print(f"{n0} merged; {n0 - n1} more duplicates/territories, {n1 - n2} outside the US, "
          f"{n2 - n3} replicas, {n3 - len(L)} beacons/towers/inland decorations removed")
    L = list(ThreadPoolExecutor(16).map(snap, L))
    for d in L:
        d["surf"] = landcheck.surface(d["lon"], d["lat"])
        d["srcs"] = sorted(d["srcs"])
        d["region"] = ("AK" if d["lat"] > 51 else "HI" if d["lon"] < -150
                       else "PR" if d["lat"] < 19 and d["lon"] > -68 else "L48")
    json.dump(L, open(CACHE / "inventory.json", "w"), indent=0)

    c = collections.Counter((d["region"], d["surf"]) for d in L)
    print(len(L), "unique lighthouses")
    print("by source:", collections.Counter("+".join(d["srcs"]) for d in L))
    print("region x surface:", dict(c))
    land48 = [d for d in L if d["region"] == "L48" and d["surf"] == "land"]
    print("lower 48 on land:", len(land48), " historic:", sum(d["historic"] for d in land48),
          " named:", sum(bool(d["name"]) for d in land48))
    for lim in (500, 1000, 2000, 5000):
        print(f"  within {lim} m of a main road:", sum(d["snap_m"] < lim for d in land48))


if __name__ == "__main__":
    main()
