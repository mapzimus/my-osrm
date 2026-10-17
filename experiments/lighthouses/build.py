"""Fastest trip to every lighthouse you can drive to: lower 48, Alaska, Hawaii.

Lighthouses: OpenStreetMap + Wikidata (inventory.py), on land, named, with a
public road to them (lastmile.py). The engine routes between the junctions
where each lighthouse's local road meets a main road; every stop adds the
out-and-back drive on that local road. Where no road connects two stops
(Hawaii's islands, Hawaii to the mainland) the leg is a flight: great-circle
distance at FLIGHT_MPH plus AIRPORT_H.

Order: open-path TSP (free start and end), OR-Tools guided local search.
Drive time only: no time at the lighthouses, no sleep.

    python experiments/lighthouses/build.py            # cached steps are skipped
    python experiments/lighthouses/build.py --resolve  # re-run the solver

Writes cache/tour.json for video.py.
"""

from __future__ import annotations

import argparse
import json
import math
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
OSRM = "http://127.0.0.1:5002"
SOLVE_SECONDS = 300
FLIGHT_MPH = 550
AIRPORT_H = 2.0
HAWAII_PENALTY = 1_000_000     # seconds, solver only (see main)


def get(url: str, timeout: float = 600) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        d = json.load(r)
    if d.get("code") != "Ok":
        raise RuntimeError(f"OSRM: {d.get('code')} {d.get('message')}")
    return d


def polyline(pts: list[list[float]]) -> str:
    """Google polyline (precision 5) of lon,lat points: keeps 600-point URLs short."""
    out, plat, plon = [], 0, 0
    for lon, lat in pts:
        for v, p in ((round(lat * 1e5), plat), (round(lon * 1e5), plon)):
            d = v - p
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        plat, plon = round(lat * 1e5), round(lon * 1e5)
    return urllib.request.quote("".join(out), safe="")


def km(a, b) -> float:
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def flight_s(a, b) -> float:
    return (km(a, b) / 1.609344 / FLIGHT_MPH + AIRPORT_H) * 3600


def matrix(stops: list[dict]) -> dict:
    path = CACHE / "matrix.json"
    names = [s["name"] for s in stops]
    if path.exists():
        m = json.load(open(path))
        if m["names"] == names:
            return m
    js = [s["junction"] for s in stops]
    d = get(f"{OSRM}/table/v1/driving/polyline({polyline(js)})?annotations=duration,distance")
    # Snapped junctions: the dots and the trail use these.
    m = dict(names=names, dur=d["durations"], dist=d["distances"],
             road=[w["location"] for w in d["sources"]])
    json.dump(m, open(path, "w"))
    return m


def islands(stops: list[dict]) -> list[int | None]:
    """Which Hawaiian island each stop is on (index of the nearest Natural
    Earth land polygon); None outside Hawaii."""
    import shapefile
    from shapely.geometry import Point, box, shape
    from shapely.strtree import STRtree
    hi_box = box(-161, 18, -154, 23)
    parts = []
    for shp in shapefile.Reader(str(HERE.parents[1] / "data/naturalearth/ne_10m_land/ne_10m_land.shp")).iterShapes():
        g = shape(shp.__geo_interface__)
        if g.intersects(hi_box):
            parts += [p for p in getattr(g.intersection(hi_box), "geoms", [g.intersection(hi_box)])
                      if p.geom_type == "Polygon"]
    tree = STRtree(parts)
    return [int(tree.nearest(Point(s["lon"], s["lat"]))) if s["region"] == "HI" else None for s in stops]


def solve(cost: list[list[float]]) -> list[int]:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2 as E

    n = len(cost)
    M = [[0] * (n + 1)] + [[0] + [int(round(v)) for v in row] for row in cost]
    mgr = pywrapcp.RoutingIndexManager(n + 1, 1, 0)
    rt = pywrapcp.RoutingModel(mgr)
    cb = rt.RegisterTransitCallback(lambda a, b: M[mgr.IndexToNode(a)][mgr.IndexToNode(b)])
    rt.SetArcCostEvaluatorOfAllVehicles(cb)
    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = E.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    p.local_search_metaheuristic = E.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.seconds = SOLVE_SECONDS
    sol = rt.SolveWithParameters(p)
    i, order = rt.Start(0), []
    while not rt.IsEnd(i):
        if node := mgr.IndexToNode(i):
            order.append(node - 1)
        i = sol.Value(rt.NextVar(i))
    return order


def drive(stops: list[dict]) -> dict:
    """One engine route through consecutive stops (all road-connected).
    Per vertex: clock, distance, and whether that segment is a ferry."""
    d = get(f"{OSRM}/route/v1/driving/polyline({polyline([s['road'] for s in stops])})"
            "?overview=full&steps=true&geometries=geojson&annotations=duration,distance"
            "&continue_straight=false")
    r = d["routes"][0]
    coords = r["geometry"]["coordinates"]
    legs, i0 = [], 0
    for leg in r["legs"]:
        ann = leg["annotation"]
        n = len(ann["duration"])
        cs = coords[i0:i0 + n + 1]
        i0 += n
        # Ferry or not per segment: match the segment's midpoint distance
        # along the leg to the step covering it.
        ends, names, acc = [], [], 0.0
        for st in leg["steps"]:
            acc += st["distance"]
            ends.append(acc)
            names.append((st.get("name") or "Ferry") if st["mode"] == "ferry" else None)
        ferry, acc, j = [None], 0.0, 0
        kd = (ends[-1] or 1.0) / (sum(ann["distance"]) or 1.0)
        for dd in ann["distance"]:
            mid = (acc + dd / 2) * kd
            while j < len(ends) - 1 and ends[j] < mid:
                j += 1
            ferry.append(names[j])
            acc += dd
        legs.append(dict(coords=cs, ferry=ferry, seg_t=ann["duration"], seg_d=ann["distance"],
                         t=leg["duration"], d=leg["distance"]))
    return legs


def label(stops: list[dict]) -> None:
    """State (Natural Earth polygons, nearest one for points just offshore)
    and nearest town (the app's places index) for the on-screen label."""
    import sqlite3

    import shapefile
    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree
    sf = shapefile.Reader(str(CACHE / "states" / "ne_10m_admin_1_states_provinces.shp"))
    geoms, names = [], []
    for s, r in zip(sf.shapes(), sf.records()):
        if r["adm0_a3"] == "USA":
            geoms.append(shape(s.__geo_interface__))
            names.append(r["name"])
    tree = STRtree(geoms)
    db = sqlite3.connect(HERE.parents[1] / "data" / "app" / "places.sqlite")
    towns = db.execute("select name, lon, lat from places where country='US' and "
                       "kind in ('city','town','village')").fetchall()
    import numpy as np
    tl = np.array([(t[1], t[2]) for t in towns])
    for s in stops:
        s["state"] = names[tree.nearest(Point(s["lon"], s["lat"]))]
        k = math.cos(math.radians(s["lat"]))
        d2 = ((tl[:, 0] - s["lon"]) * k) ** 2 + (tl[:, 1] - s["lat"]) ** 2
        s["town"] = towns[int(d2.argmin())][0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolve", action="store_true")
    args = ap.parse_args()

    stops = sorted(json.load(open(CACHE / "drivable.json")), key=lambda s: (s["lon"], s["lat"]))
    m = matrix(stops)
    for s, rd in zip(stops, m["road"]):
        s["road"] = rd
    # No car ferry runs between the Hawaiian islands (OSM still has barge and
    # passenger lines the engine treats as ferries): different island = fly.
    isl = islands(stops)
    for i in range(len(stops)):
        for j in range(len(stops)):
            if isl[i] is not None and isl[j] is not None and isl[i] != isl[j]:
                m["dur"][i][j] = m["dist"][i][j] = None
    # A stop the engine can't reach from most others (an island road with no
    # car ferry) is not drivable after all, unless it's in Hawaii (flights).
    n = len(stops)
    keep = [i for i in range(n)
            if stops[i]["region"] == "HI" or sum(m["dur"][i][j] is None for j in range(n)) < n / 2]
    if len(keep) < n:
        print("dropped (no road connection):", [stops[i]["name"] for i in range(n) if i not in keep])
        stops = [stops[i] for i in keep]
        m["dur"] = [[m["dur"][i][j] for j in keep] for i in keep]
        m["dist"] = [[m["dist"][i][j] for j in keep] for i in keep]
        n = len(stops)
    # No road between two stops (Hawaii's islands, Hawaii to anywhere): fly.
    # Crossing to or from Hawaii carries a solver-only penalty, so Hawaii is
    # one visit, not a shortcut across the mainland (Nebraska -> Hawaii ->
    # Washington beats driving otherwise). The clock uses the real flight time.
    hi = [s["region"] == "HI" for s in stops]
    cost = [[m["dur"][i][j] if m["dur"][i][j] is not None
             else flight_s(stops[i]["road"], stops[j]["road"]) + (HAWAII_PENALTY if hi[i] != hi[j] else 0)
             for j in range(n)] for i in range(n)]

    op = CACHE / "order.json"
    if args.resolve or not op.exists() or len(json.load(open(op))) != n:
        json.dump(solve(cost), open(op, "w"))
    order = json.load(open(op))
    stops = [stops[i] for i in order]

    # Split into driving runs at the flights.
    runs, cur = [], [0]
    for a, b in zip(range(n - 1), range(1, n)):
        ia, ib = order[a], order[b]
        if m["dur"][ia][ib] is None:
            runs.append(cur)
            cur = [b]
        else:
            cur.append(b)
    runs.append(cur)

    # Timeline. Each stop: drive in on the spur, drive back out.
    coords, t, dist, mode = [], [], [], []      # mode per vertex: 'road' | 'ferry' | 'flight' (segment ending here)
    clock = miles_m = 0.0

    def push(c, dt, dd, md):
        nonlocal clock, miles_m
        clock += dt
        miles_m += dd
        coords.append(c)
        t.append(clock)
        dist.append(miles_m)
        mode.append(md)

    def spur_out_back(s):
        sp = s["spur"]
        if len(sp) < 2:
            s["t"] = clock
            return
        k = s["spur_s"] / max(s["spur_m"], 1)
        for path in (sp, sp[::-1]):
            for a, b in zip(path, path[1:]):
                dd = km(a, b) * 1000
                push(b, dd * k, dd, "road")
            if path is sp:
                s["t"] = clock           # arrival: the end of the spur

    flights, legs_info, ferries = [], [], []
    for ri, run in enumerate(runs):
        rs = [stops[i] for i in run]
        if ri:
            a, b = stops[run[0] - 1], rs[0]
            fs = flight_s(a["road"], b["road"])
            t0 = clock
            push(b["road"], fs, 0, "flight")          # flown miles are counted separately
            flights.append(dict(frm=a["name"], to=b["name"], a=a["road"], b=b["road"], t0=t0, t1=clock))
        elif not coords:
            push(rs[0]["road"], 0, 0, "road")
        spur_out_back(rs[0])
        if len(rs) > 1:
            for li, leg in enumerate(drive(rs)):
                assert len(leg["seg_t"]) == len(leg["coords"]) - 1, (len(leg["seg_t"]), len(leg["coords"]))
                kt = leg["t"] / max(sum(leg["seg_t"]), 1e-9)
                kd = leg["d"] / max(sum(leg["seg_d"]), 1e-9)
                for c, dt, dd, fe in zip(leg["coords"][1:], leg["seg_t"], leg["seg_d"], leg["ferry"][1:]):
                    push(c, dt * kt, dd * kd, "ferry" if fe else "road")
                    if fe:
                        if ferries and ferries[-1]["i1"] == len(coords) - 2:
                            ferries[-1]["i1"] = len(coords) - 1
                        else:
                            ferries.append(dict(i0=len(coords) - 2, i1=len(coords) - 1, names={}))
                        ferries[-1]["names"][fe] = ferries[-1]["names"].get(fe, 0) + dd
                legs_info.append(dict(frm=rs[li]["name"], to=rs[li + 1]["name"], h=leg["t"] / 3600,
                                      mi=leg["d"] / 1609.344, ferry=any(leg["ferry"])))
                spur_out_back(rs[li + 1])

    for f in ferries:
        # The crossing's name: the named step covering the most water, not a
        # dock ramp ("Berth 4 Approach") or the generic "Ferry".
        named = {k: v for k, v in f.pop("names").items()
                 if k != "Ferry" and not any(w in k for w in ("Approach", "Berth", "Ramp", "Terminal"))}
        f["name"] = max(named, key=named.get) if named else "Ferry"
    for s in stops:
        s.pop("spur", None)
    label(stops)
    hours = clock / 3600
    fly_h = sum(f["t1"] - f["t0"] for f in flights) / 3600
    json.dump(dict(stops=stops, coords=coords, t=t, dist=dist, mode=mode, flights=flights, ferries=ferries, legs=legs_info,
                   hours=hours, fly_hours=fly_h, miles=miles_m / 1609.344,
                   flown_miles=sum(km(f["a"], f["b"]) for f in flights) / 1.609344),
              open(CACHE / "tour.json", "w"))
    ferry_n = len(ferries)
    print(f"{n} lighthouses, {hours:.1f} h total ({hours - fly_h:.1f} h driving, {len(flights)} flights {fly_h:.1f} h), "
          f"{miles_m / 1609.344:,.0f} mi driven, {ferry_n} ferry crossings, "
          f"{stops[0]['name']} -> {stops[-1]['name']}")


if __name__ == "__main__":
    main()
