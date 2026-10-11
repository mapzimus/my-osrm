"""A canned OSRM for tests: answers /route, /table, /nearest, /trip with plausible numbers.

Durations are haversine distance at 60 mph. A coordinate at exactly lon=0,lat=0
yields NoRoute (HTTP 400). Any leg crossing west of -122.4 includes a ferry step.
"""

from __future__ import annotations

import json
import math
import re
from urllib.parse import parse_qs

import httpx

calls: list[str] = []


def hav_m(a, b) -> float:
    r = 6371000.0
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    dlat, dlon = la2 - la1, math.radians(b[0] - a[0])
    h = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def parse_coords(path: str) -> list[list[float]]:
    m = re.search(r"/v1/driving/([^/?]+)", path)
    return [[float(x) for x in p.split(",")] for p in m.group(1).split(";")]


def route_json(coords: list[list[float]], steps: bool, geojson: bool) -> dict:
    legs = []
    total_d = total_t = 0.0
    for a, b in zip(coords, coords[1:]):
        d = hav_m(a, b)
        t = d / (60 * 1609.344 / 3600)
        ferry = a[0] < -122.4 or b[0] < -122.4
        leg_steps = [
            {"name": "Main St", "ref": "US 1", "mode": "driving", "distance": d * 0.4, "duration": t * 0.4,
             "maneuver": {"type": "depart", "location": a}},
            {"name": "Ferry", "ref": "", "mode": "ferry" if ferry else "driving", "distance": d * 0.6, "duration": t * 0.6,
             "maneuver": {"type": "turn", "modifier": "left", "location": b}},
            {"name": "", "mode": "driving", "distance": 0, "duration": 0, "maneuver": {"type": "arrive", "location": b}},
        ] if steps else []
        legs.append({"distance": d, "duration": t, "steps": leg_steps})
        total_d += d
        total_t += t
    geom = {"type": "LineString", "coordinates": coords} if geojson else "polyline"
    return {"code": "Ok",
            "routes": [{"distance": total_d, "duration": total_t, "geometry": geom, "legs": legs}],
            "waypoints": [{"location": c, "name": f"Road {i}", "distance": 12.0 + i} for i, c in enumerate(coords)]}


def handle(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    calls.append(path)
    q = parse_qs(request.url.query.decode())
    coords = parse_coords(path)
    if any(c == [0.0, 0.0] for c in coords):
        return httpx.Response(400, json={"code": "NoRoute", "message": "Impossible route."})
    if path.startswith("/route/"):
        return httpx.Response(200, json=route_json(coords, q.get("steps", ["false"])[0] == "true",
                                                   q.get("geometries", ["polyline"])[0] == "geojson"))
    if path.startswith("/table/"):
        n = len(coords)
        durs = [[hav_m(a, b) / 26.8 for b in coords] for a in coords]
        dists = [[hav_m(a, b) for b in coords] for a in coords]
        if "sources" in q:
            src = [int(s) for s in q["sources"][0].split(";")]
            durs, dists = [durs[i] for i in src], [dists[i] for i in src]
        return httpx.Response(200, json={"code": "Ok", "durations": durs, "distances": dists,
                                         "destinations": [{"location": c, "distance": 20.0, "name": "X"} for c in coords],
                                         "sources": [{"location": c, "distance": 20.0} for c in coords]})
    if path.startswith("/nearest/"):
        n = int(q.get("number", ["1"])[0])
        return httpx.Response(200, json={"code": "Ok", "waypoints": [
            {"location": [coords[0][0] + 0.0001 * i, coords[0][1]], "name": f"Dock Rd {i}", "distance": 30.0 + i,
             "nodes": [1000 + i, 2000 + i], "hint": "h"} for i in range(n)]})
    if path.startswith("/trip/"):
        r = route_json(coords, True, True)
        wps = [{"location": c, "name": "", "distance": 1.0, "waypoint_index": len(coords) - 1 - i, "trips_index": 0}
               for i, c in enumerate(coords)]
        return httpx.Response(200, json={"code": "Ok", "trips": r["routes"], "waypoints": wps})
    return httpx.Response(404, json={"code": "InvalidUrl"})
