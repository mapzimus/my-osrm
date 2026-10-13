"""Fastest drive through every college in New England.

Colleges: IPEDS 2024 directory (NCES), active degree-granting institutions in
CT, MA, ME, NH, RI, VT, minus system offices and online-only units. Order: open-path TSP (free start and end) on the
na-ferry engine's drive-time matrix, solved with OR-Tools guided local search.
Drive time only: no time on campus, no sleep.

    python experiments/college_tour/build.py            # cached steps are skipped
    python experiments/college_tour/build.py --resolve  # re-run the solver

Writes cache/tour.json for video.py.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
OSRM = "http://127.0.0.1:5002"
IPEDS = "https://nces.ed.gov/ipeds/datacenter/data/HD2024.zip"
STATES = {"CT", "MA", "ME", "NH", "RI", "VT"}
SOLVE_SECONDS = 240


def colleges() -> list[dict]:
    path = CACHE / "HD2024.csv"
    if not path.exists():
        with urllib.request.urlopen(IPEDS, timeout=120) as r:
            zipfile.ZipFile(io.BytesIO(r.read())).extract("HD2024.csv", CACHE)
    rows = csv.DictReader(open(path, encoding="latin-1"))
    return [dict(name=x["INSTNM"], city=x["CITY"], st=x["STABBR"],
                 lon=float(x["LONGITUD"]), lat=float(x["LATITUDE"]))
            for x in rows
            if x["STABBR"] in STATES and x["DEGGRANT"] == "1" and x["CYACTIVE"] == "1"
            # Not campuses: system/chancellor offices (sector 0), online-only units.
            and x["SECTOR"] != "0" and "Online" not in x["INSTNM"]]


def get(url: str, timeout: float = 300) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        d = json.load(r)
    if d.get("code") != "Ok":
        raise RuntimeError(f"OSRM: {d.get('code')} {d.get('message')}")
    return d


def fmt(cs) -> str:
    return ";".join(f"{c['lon']:.6f},{c['lat']:.6f}" for c in cs)


def matrix(cs: list[dict]) -> dict:
    path = CACHE / "matrix.json"
    if path.exists():
        m = json.load(open(path))
        if [c["name"] for c in m["colleges"]] == [c["name"] for c in cs]:
            return m
    d = get(f"{OSRM}/table/v1/driving/{fmt(cs)}?annotations=duration,distance")
    m = dict(colleges=cs, dur=d["durations"], dist=d["distances"])
    json.dump(m, open(path, "w"))
    return m


def solve(dur: list[list[float]]) -> list[int]:
    """Open path: a dummy depot (node 0) costs nothing to leave or reach, so
    the solver also picks the best start and end college."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2 as E

    n = len(dur)
    M = [[0] * (n + 1)] + [[0] + [int(round(v)) for v in row] for row in dur]
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolve", action="store_true")
    args = ap.parse_args()
    CACHE.mkdir(exist_ok=True)

    cs = colleges()
    m = matrix(cs)
    op = CACHE / "order.json"
    if args.resolve or not op.exists():
        json.dump(solve(m["dur"]), open(op, "w"))
    order = json.load(open(op))
    stops = [cs[i] for i in order]

    # One route through all stops, in order. Per-segment durations line up with
    # the geometry, so every vertex gets a clock time.
    d = get(f"{OSRM}/route/v1/driving/{fmt(stops)}"
            "?overview=full&geometries=geojson&annotations=duration,distance&continue_straight=false")
    r = d["routes"][0]
    coords = r["geometry"]["coordinates"]
    seg_t = [t for leg in r["legs"] for t in leg["annotation"]["duration"]]
    seg_d = [x for leg in r["legs"] for x in leg["annotation"]["distance"]]
    assert len(seg_t) == len(coords) - 1, (len(seg_t), len(coords))
    t, dist = [0.0], [0.0]
    for a, b in zip(seg_t, seg_d):
        t.append(t[-1] + a)
        dist.append(dist[-1] + b)
    # Arrival clock at each stop: leg durations (the annotation sum can drift
    # from the leg total by rounding, so scale the clock to match).
    arrive = [0.0]
    for leg in r["legs"]:
        arrive.append(arrive[-1] + leg["duration"])
    k = r["duration"] / t[-1]
    t = [v * k for v in t]
    kd = r["distance"] / dist[-1]
    dist = [v * kd for v in dist]
    legs = [dict(h=leg["duration"] / 3600, mi=leg["distance"] / 1609.344) for leg in r["legs"]]
    for s, a, wp in zip(stops, arrive, d["waypoints"]):
        s["t"] = a
        s["road"] = wp["location"]       # where the drive actually stops (dots go here)
    json.dump(dict(stops=stops, coords=coords, t=t, dist=dist, legs=legs,
                   hours=r["duration"] / 3600, miles=r["distance"] / 1609.344),
              open(CACHE / "tour.json", "w"))
    print(f"{len(stops)} colleges, {r['duration'] / 3600:.1f} h, {r['distance'] / 1609.344:,.0f} mi, "
          f"{stops[0]['name']} -> {stops[-1]['name']}")


if __name__ == "__main__":
    main()
