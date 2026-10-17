"""Optimal Drives: every 2-digit interstate, end to end, in number order.

Interstates: OpenStreetMap route relations (network=US:I, ref of one or two
digits; no suffixed splits like 35E). A number can be several separate pieces
(I-76 in Pennsylvania and Colorado); all of a number's pieces are driven
before the next number. Each piece is driven end to end on the na-ferry
engine, held to the interstate by via points every VIA_KM along its OSM line
(each with a bearing, so it snaps to the right carriageway).

Order is fixed (lowest number first). Free choices: which piece of a number
first, which way each piece is driven, where the trip starts. Solved exactly
by dynamic programming over "which end did we finish at".

    sh experiments/interstates/extract.sh
    python experiments/interstates/build.py

Writes cache/tour.json for video.py.
"""

from __future__ import annotations

import collections
import heapq
import itertools
import json
import math
import re
import sys
import urllib.error
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
sys.path.insert(0, str(HERE.parent / "lighthouses"))
import build as LH  # noqa: E402  (get, polyline)

OSRM = LH.OSRM
VIA_KM = 60          # one via point this often along a piece
GAP_KM = 25          # mapping gaps in a relation shorter than this are bridged
MIN_PIECE_KM = 5     # shorter disconnected bits are mapping debris, not pieces
MAX_PERM = 6         # pieces per number tried in every order (more: nearest-first)


# --- OSM ---------------------------------------------------------------------------

def unesc(s: str) -> str:
    return re.sub(r"%([0-9a-fA-F]+)%", lambda m: chr(int(m.group(1), 16)), s)


def relations() -> dict[str, set[int]]:
    """ref -> way ids, for 1- and 2-digit interstates."""
    out = collections.defaultdict(set)
    for line in open(CACHE / "rel.opl", encoding="utf-8"):
        if not line.startswith("r"):
            continue
        f = {p[0]: p[1:] for p in line.rstrip("\n").split(" ")}
        tags = dict(unesc(kv).split("=", 1) for kv in f.get("T", "").split(",") if "=" in kv)
        ref = tags.get("ref", "")
        if tags.get("network") != "US:I" or not re.fullmatch(r"\d{1,2}", ref):
            continue
        for m in f.get("M", "").split(","):
            if m.startswith("w"):
                out[ref].add(int(m[1:].split("@")[0]))
    return out


def way_lines(ids: set[int]) -> dict[int, list]:
    out = {}
    for line in open(CACHE / "ways.geojsonseq", encoding="utf-8"):
        f = json.loads(line.lstrip("\x1e"))
        p = f["properties"]
        if p.get("@type") == "way" and p.get("@id") in ids:
            out[p["@id"]] = f["geometry"]["coordinates"]
    return out


# --- geometry ----------------------------------------------------------------------

def km(a, b) -> float:
    return LH.km(a, b)


def bearing(a, b) -> int:
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    dl = math.radians(b[0] - a[0])
    y = math.sin(dl) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl)
    return int(round(math.degrees(math.atan2(y, x)))) % 360


def pieces(lines: list[list]) -> list[list]:
    """The separate pieces of one interstate, each as an ordered polyline
    from one end to the other (graph path between the two farthest ends)."""
    key = lambda c: (round(c[0], 5), round(c[1], 5))  # noqa: E731
    G = collections.defaultdict(dict)
    for ln in lines:
        for a, b in zip(ln, ln[1:]):
            ka, kb = key(a), key(b)
            if ka != kb:
                G[ka][kb] = G[kb][ka] = km(a, b)
    # Components, then bridge mapping gaps: join the two closest components
    # while they are within GAP_KM (cheap: compare sampled nodes).
    seen, comps = set(), []
    for n in G:
        if n in seen:
            continue
        stack, comp = [n], []
        seen.add(n)
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in G[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        comps.append(comp)
    while True:
        best = (GAP_KM, None)
        for i, j in itertools.combinations(range(len(comps)), 2):
            A, B = comps[i][:: max(1, len(comps[i]) // 300)], comps[j][:: max(1, len(comps[j]) // 300)]
            for a in A:
                for b in B:
                    d = km(a, b)
                    if d < best[0]:
                        best = (d, (i, j, a, b))
        if best[1] is None:
            break
        i, j, a, b = best[1]
        G[a][b] = G[b][a] = best[0] * 1.5        # a bridge costs a bit more than road
        comps[i] += comps.pop(j)
    out = []
    for comp in comps:
        total = sum(G[u][v] for u in comp for v in G[u]) / 2
        if total < MIN_PIECE_KM:
            continue
        a = max(comp, key=lambda u: km(u, comp[0]))
        b = max(comp, key=lambda u: km(u, a))
        out.append(path(G, a, b))
    return out


def path(G, a, b) -> list:
    dist, prev, pq = {a: 0.0}, {}, [(0.0, a)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == b:
            break
        if d > dist[u]:
            continue
        for v, w in G[u].items():
            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v], prev[v] = nd, u
                heapq.heappush(pq, (nd, v))
    out = [b]
    while out[-1] != a:
        out.append(prev[out[-1]])
    return [list(p) for p in out[::-1]]


def vias(line: list) -> list[tuple[list, int]]:
    """Points every VIA_KM along the line, with the direction of travel."""
    out, acc = [(line[0], bearing(line[0], line[1]))], 0.0
    for a, b in zip(line, line[1:]):
        acc += km(a, b)
        if acc >= VIA_KM:
            out.append((b, bearing(a, b)))
            acc = 0.0
    end = (line[-1], bearing(line[-2], line[-1]))
    if km(out[-1][0], end[0]) < VIA_KM / 3 and len(out) > 1:
        out[-1] = end
    else:
        out.append(end)
    return out


# --- engine -------------------------------------------------------------------------

def drive(line: list) -> dict:
    """Engine route along one piece, start to end."""
    vs = vias(line)
    # Tight first (right carriageway); a via the engine can't snap (a ramp-only
    # stretch, a gap in its main-road network) loosens, then drops bearings.
    for tol, rad in ((45, 2000), (90, 5000), (None, 10000)):
        q = dict(overview="full", geometries="geojson", annotations="duration,distance", continue_straight="true",
                 radiuses=";".join(str(rad) for _ in vs))
        if tol:
            q["bearings"] = ";".join(f"{b},{tol}" for _, b in vs)
        try:
            d = LH.get(f"{OSRM}/route/v1/driving/polyline({LH.polyline([p for p, _ in vs])})?"
                       + urllib.parse.urlencode(q))
            break
        except urllib.error.HTTPError:
            if tol is None:
                raise
    r = d["routes"][0]
    return dict(coords=r["geometry"]["coordinates"], h=r["duration"] / 3600, mi=r["distance"] / 1609.344,
                seg_t=[t for leg in r["legs"] for t in leg["annotation"]["duration"]],
                seg_d=[x for leg in r["legs"] for x in leg["annotation"]["distance"]],
                start=d["waypoints"][0]["location"], end=d["waypoints"][-1]["location"])


def connectors(P: list[dict]) -> list[list]:
    """Drive time between every piece end (2i = forward start, 2i+1 = forward end)."""
    path = CACHE / "connectors.json"
    pts = [x for p in P for x in (p["fw"]["start"], p["fw"]["end"])]
    if path.exists():
        c = json.load(open(path))
        if c["pts"] == pts:
            return c["dur"]
    d = LH.get(f"{OSRM}/table/v1/driving/polyline({LH.polyline(pts)})?annotations=duration")["durations"]
    json.dump(dict(pts=pts, dur=d), open(path, "w"))
    return d


def solve(P: list[dict], C: list[list]) -> list[tuple[int, str]]:
    """Fastest (piece, 'fw'|'bw') sequence with numbers in ascending order."""
    INF = float("inf")
    ends = lambda i, o: (2 * i, 2 * i + 1) if o == "fw" else (2 * i + 1, 2 * i)  # noqa: E731
    cost = lambda i, o: P[i][o]["h"] * 3600  # noqa: E731
    conn = lambda a, b: 0.0 if a == b else (C[a][b] if C[a][b] is not None else INF)  # noqa: E731
    nums = sorted({p["num"] for p in P}, key=int)
    dp = {None: (0.0, [])}                         # end point -> (seconds, plan)
    for n in nums:
        idx = [i for i, p in enumerate(P) if p["num"] == n]
        orders = (itertools.permutations(idx) if len(idx) <= MAX_PERM
                  else [sorted(idx, key=lambda i: P[i]["line"][0][0]), sorted(idx, key=lambda i: -P[i]["line"][0][0])])
        opts = {}                                  # (start, end) -> (seconds, plan)
        for order in orders:
            for ori in itertools.product(("fw", "bw"), repeat=len(order)):
                t, prev, plan = 0.0, None, []
                for i, o in zip(order, ori):
                    a, b = ends(i, o)
                    t += (conn(prev, a) if prev is not None else 0.0) + cost(i, o)
                    prev = b
                    plan.append((i, o))
                st = ends(order[0], ori[0])[0]
                if t < opts.get((st, prev), (INF,))[0]:
                    opts[st, prev] = (t, plan)
        new = {}
        for (st, en), (t, plan) in opts.items():
            for e0, (t0, plan0) in dp.items():
                tt = t0 + t + (conn(e0, st) if e0 is not None else 0.0)
                if tt < new.get(en, (INF,))[0]:
                    new[en] = (tt, plan0 + plan)
        dp = new
    best = min(dp.values(), key=lambda v: v[0])
    print(f"solved: {best[0] / 3600:.1f} h")
    return best[1]


def label(pts: list) -> list[tuple[str, str]]:
    """(town, state abbreviation) nearest each point."""
    import sqlite3

    import numpy as np
    import shapefile
    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree
    sf = shapefile.Reader(str(HERE.parents[1] / "data/naturalearth/ne_10m_admin_1_states_provinces"
                              / "ne_10m_admin_1_states_provinces.shp"))
    geoms, names = [], []
    for sh, r in zip(sf.shapes(), sf.records()):
        if r["adm0_a3"] == "USA":
            geoms.append(shape(sh.__geo_interface__))
            names.append(r["postal"])
    tree = STRtree(geoms)
    db = sqlite3.connect(HERE.parents[1] / "data/app/places.sqlite")
    towns = db.execute("select name, lon, lat, population from places where country='US' and "
                       "kind in ('city','town')").fetchall()
    tl = np.array([(t[1], t[2]) for t in towns])
    out = []
    for lon, lat in pts:
        k = math.cos(math.radians(lat))
        d2 = ((tl[:, 0] - lon) * k) ** 2 + (tl[:, 1] - lat) ** 2
        out.append((towns[int(d2.argmin())][0], names[tree.nearest(Point(lon, lat))]))
    return out


def heading(num: str, a, b) -> str:
    """Signed direction: even numbers run east-west, odd north-south."""
    if int(num) % 2 == 0:
        return "eastbound" if b[0] > a[0] else "westbound"
    return "northbound" if b[1] > a[1] else "southbound"


def timeline(P: list[dict], plan: list[tuple[int, str]]) -> dict:
    coords, t, dist, mode, segs = [], [], [], [], []
    clock = metres = 0.0

    def push(r: dict, md: str) -> None:
        nonlocal clock, metres
        cs = r["coords"]
        kt = r["h"] * 3600 / max(sum(r["seg_t"]), 1e-9)
        kd = r["mi"] * 1609.344 / max(sum(r["seg_d"]), 1e-9)
        if not coords:
            coords.append(cs[0]); t.append(0.0); dist.append(0.0); mode.append(md)  # noqa: E702
        for c, dt, dd in zip(cs[1:], r["seg_t"], r["seg_d"]):
            clock += dt * kt
            metres += dd * kd
            coords.append(c); t.append(clock); dist.append(metres); mode.append(md)  # noqa: E702

    prev = None
    for i, o in plan:
        r = P[i][o]
        if prev is not None and km(prev, r["start"]) > 0.05:
            d = LH.get(f"{OSRM}/route/v1/driving/polyline({LH.polyline([prev, r['start']])})"
                       "?overview=full&geometries=geojson&annotations=duration,distance")["routes"][0]
            push(dict(coords=d["geometry"]["coordinates"], h=d["duration"] / 3600, mi=d["distance"] / 1609.344,
                      seg_t=[x for leg in d["legs"] for x in leg["annotation"]["duration"]],
                      seg_d=[x for leg in d["legs"] for x in leg["annotation"]["distance"]]), "connector")
        i0, t0 = len(coords) - 1, clock
        push(r, "interstate")
        segs.append(dict(num=P[i]["num"], i0=i0, i1=len(coords) - 1, t0=t0, t1=clock, h=r["h"], mi=r["mi"],
                         a=r["start"], b=r["end"], dir=heading(P[i]["num"], r["start"], r["end"])))
        prev = r["end"]
    for sg, (fa, fb) in zip(segs, zip(label([s["a"] for s in segs]), label([s["b"] for s in segs]))):
        sg["from"], sg["to"] = f"{fa[0]}, {fa[1]}", f"{fb[0]}, {fb[1]}"
    on = sum(s["t1"] - s["t0"] for s in segs) / 3600
    return dict(coords=coords, t=t, dist=dist, mode=mode, segs=segs, hours=clock / 3600,
                interstate_hours=on, miles=metres / 1609.344,
                interstate_miles=sum(s["mi"] for s in segs), numbers=len({s["num"] for s in segs}))


def main() -> None:
    if "--finish" in sys.argv:              # pieces.json cached: just re-solve
        finish(json.load(open(CACHE / "pieces.json")))
        return
    rels = relations()
    nums = sorted(rels, key=int)
    print(len(nums), "interstates:", " ".join(nums))
    lines = way_lines(set().union(*rels.values()))
    P = []                   # pieces: number, line, both directions driven
    for n in nums:
        for line in pieces([lines[w] for w in rels[n] if w in lines]):
            fw, bw = drive(line), drive(line[::-1])
            P.append(dict(num=n, line=line, fw=fw, bw=bw))
            print(f"  I-{n}: {fw['mi']:,.0f} mi, {fw['h']:.1f} h / {bw['h']:.1f} h")
    json.dump(P, open(CACHE / "pieces.json", "w"))
    print(len(P), "pieces")
    finish(P)


def finish(P: list[dict]) -> None:
    plan = solve(P, connectors(P))
    tour = timeline(P, plan)
    # For the video: each piece's two ends pace the camera; the whole network
    # draws faint underneath. No flights or ferries on this one.
    tour["stops"] = [dict(name=f"I-{sg['num']} {k}", lon=pt[0], lat=pt[1], t=t, region="US")
                     for sg in tour["segs"] for k, pt, t in (("start", sg["a"], sg["t0"]), ("end", sg["b"], sg["t1"]))]
    tour["network"] = [p["line"] for p in P]
    tour.update(flights=[], ferries=[], fly_hours=0.0)
    json.dump(tour, open(CACHE / "tour.json", "w"))
    s0, s1 = tour["segs"][0], tour["segs"][-1]
    print(f"{tour['numbers']} interstates, {len(tour['segs'])} pieces, {tour['hours']:.1f} h, "
          f"{tour['miles']:,.0f} mi ({tour['interstate_miles']:,.0f} on the interstates), "
          f"start {s0['from']} (I-{s0['num']}), finish {s1['to']} (I-{s1['num']})")


if __name__ == "__main__":
    main()
