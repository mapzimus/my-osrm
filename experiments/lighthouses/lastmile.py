"""The last stretch: from a main road to the lighthouse, on OSM's local roads.

The na-ferry engine only has main roads (motorway to tertiary), and most
lighthouses sit at the end of a small local road. For each candidate this
walks the real local road network (cache/roads.geojsonseq, cut out around the
lighthouses) from the road nearest the light to the first main-road node.

A lighthouse counts as drivable when a public road (not private, not a
footpath) comes within WALK_M of it and connects to a main road. That rules
out reef and crib lights, roadless islands and gated Coast Guard property.

Writes cache/drivable.json: one record per drivable lighthouse with the
junction (where the engine route goes) and the spur's time, length, shape.
"""

from __future__ import annotations

import collections
import heapq
import json
import math
from pathlib import Path

from shapely.geometry import Point
from shapely.strtree import STRtree

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
WALK_M = 800            # park and walk at most this far (a 10-minute walk)
MAX_SPUR_M = 25_000     # give up searching past this much local road
# km/h, from OSRM's car.lua defaults (the engine's own profile).
SPEED = dict(motorway=90, motorway_link=45, trunk=85, trunk_link=40, primary=65, primary_link=30,
             secondary=55, secondary_link=25, tertiary=40, tertiary_link=20, unclassified=25,
             residential=25, living_street=10, service=15, road=25)
MAJOR = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
         "secondary", "secondary_link", "tertiary", "tertiary_link"}
CLOSED = {"private", "no"}


def metres(a, b) -> float:
    k = math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * k, a[1] - b[1]) * 111_320


def graph():
    ids: dict[tuple, int] = {}
    xy: list[tuple] = []
    adj: dict[int, list] = collections.defaultdict(list)
    major: set[int] = set()

    def node(c) -> int:
        k = (round(c[0], 7), round(c[1], 7))
        if k not in ids:
            ids[k] = len(xy)
            xy.append(k)
        return ids[k]

    for line in open(CACHE / "roads.geojsonseq", encoding="utf-8"):
        f = json.loads(line.lstrip("\x1e"))
        p = f["properties"]
        hw = p.get("highway")
        if hw not in SPEED:
            continue
        if any(p.get(k) in CLOSED for k in ("access", "motor_vehicle", "motorcar", "vehicle")):
            continue
        cs = f["geometry"]["coordinates"]
        v = SPEED[hw] / 3.6
        prev = node(cs[0])
        for c in cs[1:]:
            cur = node(c)
            d = metres(xy[prev], xy[cur])
            adj[prev].append((cur, d / v, d))
            adj[cur].append((prev, d / v, d))
            if hw in MAJOR:
                major.add(prev)
                major.add(cur)
            prev = cur
    return xy, adj, major


def spur(start: list[tuple[int, float]], adj, major) -> tuple | None:
    """Dijkstra by time from the start nodes to the nearest main-road node.
    Returns (junction node, seconds, metres, node path) or None."""
    best = {}
    prev = {}
    h = [(0.0, 0.0, n) for n, _ in start]
    for _, _, n in h:
        best[n] = 0.0
    heapq.heapify(h)
    while h:
        t, d, n = heapq.heappop(h)
        if t > best.get(n, math.inf) or d > MAX_SPUR_M:
            continue
        if n in major:
            path = [n]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            return n, t, d, path
        for m, dt, dd in adj.get(n, ()):
            if t + dt < best.get(m, math.inf):
                best[m] = t + dt
                prev[m] = n
                heapq.heappush(h, (t + dt, d + dd, m))
    return None


def main() -> None:
    L = json.load(open(CACHE / "inventory.json"))
    cand = [d for d in L if d["region"] in ("L48", "AK", "HI") and d["surf"] != "water" and d["name"]]
    xy, adj, major = graph()
    print(f"{len(cand)} named land candidates; road graph {len(xy):,} nodes")

    pts = [Point(c) for c in xy]
    tree = STRtree(pts)
    out, why = [], collections.Counter()
    for d in cand:
        here = (d["lon"], d["lat"])
        deg = WALK_M / 111_320 / math.cos(math.radians(d["lat"]))
        near = [(int(i), metres(here, xy[i])) for i in tree.query(Point(here).buffer(deg))]
        near = [(i, m) for i, m in near if m <= WALK_M and i in adj]
        if not near:
            why["no public road within a 10-min walk"] += 1
            continue
        r = spur(near, adj, major)
        if r is None:
            why["local roads never reach a main road"] += 1
            continue
        j, t, m, path = r
        walk = min(mm for i, mm in near if i == path[-1]) if path else 0
        out.append(dict(d, junction=list(xy[j]), spur_s=t, spur_m=m, walk_m=walk,
                        spur=[list(xy[i]) for i in path]))   # junction -> lighthouse end
        why["drivable"] += 1
    json.dump(out, open(CACHE / "drivable.json", "w"))
    print(dict(why))
    by = collections.Counter(d["region"] for d in out)
    print("drivable by region:", dict(by))
    s = sorted(out, key=lambda d: -d["spur_s"])
    print("longest spurs:", [(d["name"], round(d["spur_s"] / 60, 1), round(d["spur_m"] / 1609.3, 1)) for d in s[:8]])
    print("median spur min:", round(sorted(d["spur_s"] for d in out)[len(out) // 2] / 60, 1))


if __name__ == "__main__":
    main()
