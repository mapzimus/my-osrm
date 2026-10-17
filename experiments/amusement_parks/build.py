"""Fastest trip to every amusement park in North America (adapted from
lighthouses/build.py).

Parks: cache/parks.json from inventory.py (a roller coaster or real water
slides). The engine routes to the main road nearest each park. Where no road
connects two stops (Hawaii, Puerto Rico) the leg is a flight: great-circle
distance at FLIGHT_MPH plus AIRPORT_H.

Order: open-path TSP (free start and end), OR-Tools guided local search.
Drive time only: no time in the parks, no sleep.

    python experiments/amusement_parks/build.py            # cached steps are skipped
    python experiments/amusement_parks/build.py --resolve  # re-run the solver

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
SOLVE_SECONDS = 600            # 445 stops: longer than the lighthouses' 300 s
FLIGHT_MPH = 550
AIRPORT_H = 2.0
ISLAND_PENALTY = 1_000_000     # seconds, solver only (see main)
FLY = ("HI", "PR")             # regions reached by plane
COUNTRY = {"us": "US", "canada": "CA", "mexico": "MX", "puerto-rico": "PR"}
# Island parks are flown to from a hub on the way (Max, 2026-10-13): drive to
# the hub, fly to the island's airport, drive to the park, and back the same
# way. The solver picks the hub. Hubs listed have nonstops to that island.
AIRPORTS = {"BOS": (-71.0096, 42.3656), "JFK": (-73.7781, 40.6413), "EWR": (-74.1745, 40.6895),
            "PHL": (-75.2424, 39.8744), "IAD": (-77.4565, 38.9531), "CLT": (-80.9431, 35.2140),
            "ATL": (-84.4277, 33.6407), "MCO": (-81.3089, 28.4312), "MIA": (-80.2870, 25.7959),
            "FLL": (-80.1527, 26.0742), "DTW": (-83.3534, 42.2162), "ORD": (-87.9073, 41.9742),
            "MSP": (-93.2218, 44.8848), "DFW": (-97.0403, 32.8998), "IAH": (-95.3368, 29.9902),
            "DEN": (-104.6737, 39.8561), "PHX": (-112.0116, 33.4352), "LAS": (-115.1398, 36.0840),
            "LAX": (-118.4085, 33.9416), "SFO": (-122.3790, 37.6213), "SEA": (-122.3088, 47.4502),
            "PDX": (-122.5951, 45.5898), "YVR": (-123.1815, 49.1967), "YYZ": (-79.6248, 43.6777),
            "YUL": (-73.7408, 45.4706),
            "SJU": (-66.0018, 18.4394), "HNL": (-157.9251, 21.3245)}
ISLAND_AIRPORT = {"PR": "SJU", "HI": "HNL"}
HUBS = {"PR": ["BOS", "JFK", "EWR", "PHL", "IAD", "CLT", "ATL", "MCO", "MIA", "FLL", "ORD", "DTW",
               "DFW", "IAH", "YYZ", "YUL"],
        "HI": ["LAX", "SFO", "SEA", "PDX", "PHX", "LAS", "DEN", "DFW", "IAH", "ORD", "ATL", "MSP", "YVR"]}


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
        if m["names"] == names and m.get("junctions") == [s["junction"] for s in stops]:
            return m
    js = [s["junction"] for s in stops]
    d = get(f"{OSRM}/table/v1/driving/polyline({polyline(js)})?annotations=duration,distance")
    # Snapped junctions: the dots and the trail use these.
    m = dict(names=names, dur=d["durations"], dist=d["distances"],
             road=[w["location"] for w in d["sources"]], junctions=js)
    json.dump(m, open(path, "w"))
    return m


def resnap(stops: list[dict], m: dict) -> bool:
    """A park whose point snaps to a road stub the engine can leave but not
    enter (Islands of Adventure) gets the next-nearest road that works both
    ways with a well-connected park. Returns True if any junction moved."""
    n, moved = len(stops), False
    ok = min(range(n), key=lambda i: sum(m["dur"][i][j] is None or m["dur"][j][i] is None for j in range(n)))
    for i, s in enumerate(stops):
        bad = sum(m["dur"][i][j] is None or m["dur"][j][i] is None for j in range(n))
        if s["region"] in FLY or bad < n / 2:
            continue
        near = get(f"{OSRM}/nearest/v1/driving/{s['lon']},{s['lat']}?number=12")["waypoints"]
        for w in near[1:]:
            pts = [w["location"], stops[ok]["road"]]
            d = get(f"{OSRM}/table/v1/driving/polyline({polyline(pts)})?annotations=duration")["durations"]
            if d[0][1] is not None and d[1][0] is not None:
                print(f"  resnapped {s['name']}: {w['distance']:.0f} m from the park point")
                s["junction"], moved = w["location"], True
                break
    return moved


def air_matrix(stops: list[dict]) -> dict:
    """Drive times between every stop and every airport (both ways)."""
    path = CACHE / "air_matrix.json"
    names = [s["name"] for s in stops] + list(AIRPORTS)
    pts = [s["road"] for s in stops] + [list(v) for v in AIRPORTS.values()]
    if path.exists():
        m = json.load(open(path))
        if m["names"] == names and m.get("pts") == pts:
            return m
    n = len(stops)
    ids = ";".join(map(str, range(n, len(pts))))
    to = get(f"{OSRM}/table/v1/driving/polyline({polyline(pts)})?destinations={ids}&annotations=duration")
    frm = get(f"{OSRM}/table/v1/driving/polyline({polyline(pts)})?sources={ids}&annotations=duration")
    m = dict(names=names, to=[row for row in to["durations"]], frm=frm["durations"],
             road={c: w["location"] for c, w in zip(AIRPORTS, frm["sources"])}, pts=pts)
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


def solve(cost: list[list[float]], no_ends: tuple = ()) -> list[int]:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2 as E

    n = len(cost)
    M = [[0] * (n + 1)] + [[0] + [int(round(v)) for v in row] for row in cost]
    for k in no_ends:                  # can't start or finish here: islands are round trips
        M[0][k + 1] = M[k + 1][0] = 10 * ISLAND_PENALTY
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
    sf = shapefile.Reader(str(HERE.parents[1] / "data" / "naturalearth" / "ne_10m_admin_1_states_provinces"
                              / "ne_10m_admin_1_states_provinces.shp"))
    geoms, names = [], []
    for s, r in zip(sf.shapes(), sf.records()):
        if r["adm0_a3"] in ("USA", "CAN", "MEX", "PRI"):
            geoms.append(shape(s.__geo_interface__))
            names.append(r["name"])
    tree = STRtree(geoms)
    db = sqlite3.connect(HERE.parents[1] / "data" / "app" / "places.sqlite")
    towns = db.execute("select name, lon, lat from places where country in ('US','CA','MX','PR') and "
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

    stops = sorted(json.load(open(CACHE / "parks.json", encoding="utf-8")), key=lambda s: (s["lon"], s["lat"]))
    for s in stops:
        hawaii = -161 < s["lon"] < -154 and 18 < s["lat"] < 23
        s["region"] = "HI" if hawaii else COUNTRY[s["region"]]
        s["junction"], s["spur"], s["spur_s"], s["spur_m"] = [s["lon"], s["lat"]], [], 0.0, 0.0
    m = matrix(stops)
    for s, rd in zip(stops, m["road"]):
        s["road"] = rd
    if resnap(stops, m):
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
            if stops[i]["region"] in FLY or sum(m["dur"][i][j] is None for j in range(n)) < n / 2]
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
    hi = [s["region"] if s["region"] in FLY else "" for s in stops]
    am = air_matrix(stops)
    codes = list(AIRPORTS)
    ap = {c: k for k, c in enumerate(codes)}
    via = {}                                   # (i, j) -> (departure, arrival airport)

    def hop(i: int, j: int) -> float:
        """Drive to an airport, fly, drive on: the best pair of airports."""
        dep = [ISLAND_AIRPORT[hi[i]]] if hi[i] else HUBS[hi[j]] if hi[j] else codes
        arr = [ISLAND_AIRPORT[hi[j]]] if hi[j] else HUBS[hi[i]] if hi[i] else codes
        best = (float("inf"), None)
        for a in dep:
            for b in arr:
                da, db = am["to"][i][ap[a]], am["frm"][ap[b]][j]
                if a != b and da is not None and db is not None:
                    c = da + flight_s(AIRPORTS[a], AIRPORTS[b]) + db
                    best = min(best, (c, (a, b)), key=lambda x: x[0])
        via[i, j] = best[1]
        return best[0]

    cost = [[m["dur"][i][j] if m["dur"][i][j] is not None
             else hop(i, j) + (ISLAND_PENALTY if hi[i] != hi[j] else 0)
             + (10 * ISLAND_PENALTY if hi[i] and hi[j] and hi[i] != hi[j] else 0)   # islands connect via the mainland
             for j in range(n)] for i in range(n)]

    op = CACHE / "order.json"
    if args.resolve or not op.exists() or len(json.load(open(op))) != n:
        json.dump(solve(cost, tuple(i for i in range(n) if hi[i])), open(op, "w"))
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

    def drive_run(rs: list[dict]) -> None:
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

    for ri, run in enumerate(runs):
        rs = [stops[i] for i in run]
        if ri:
            a, b = stops[run[0] - 1], rs[0]
            ca, cb = via[order[run[0] - 1], order[run[0]]]
            pa, pb = am["road"][ca], am["road"][cb]
            drive_run([a, dict(name=ca, road=pa, spur=[])])
            t0 = clock
            push(pb, flight_s(AIRPORTS[ca], AIRPORTS[cb]), 0, "flight")   # flown miles are counted separately
            flights.append(dict(frm=ca, to=cb, a=pa, b=pb, t0=t0, t1=clock, a_town=ca, b_town=cb))
            drive_run([dict(name=cb, road=pb, spur=[]), b])
        elif not coords:
            push(rs[0]["road"], 0, 0, "road")
        spur_out_back(rs[0])
        drive_run(rs)

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
    print(f"{n} parks, {hours:.1f} h total ({hours - fly_h:.1f} h driving, {len(flights)} flights {fly_h:.1f} h), "
          f"{miles_m / 1609.344:,.0f} mi driven, {ferry_n} ferry crossings, "
          f"{stops[0]['name']} -> {stops[-1]['name']}")


if __name__ == "__main__":
    main()
