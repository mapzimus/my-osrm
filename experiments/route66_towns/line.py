"""Route 66 as one line, Chicago to Santa Monica (Max, 2026-10-16: every town along Route 66, in the
I-90 video's style; "do the loop but note the detour").

Source: OSM superroute 93155 "Historic U.S. Route 66" (network US:US:Historic), extracted with
osmium getid -r from data/us-latest.osm.pbf into data/scratch/route66_ids.geojson (way ids kept),
its relation tree in cache/relations.json (OSM API). The tree also carries older alignments (IL
1926-1930 and 1930-1940, MO 1936-1965): left out. Every other way is a graph edge; the line is the
shortest path over them from Adams St & Michigan Ave, Chicago to the Santa Monica Pier, which
picks one carriageway where the route is split.

New Mexico: OSM maps only the 1937 bypass (Santa Rosa - Moriarty - Albuquerque - Laguna). The
original 1926-1937 road looped north through Santa Fe and south through Los Lunas; OSM has no
relation for it, so the loop is driven by today's roads through its towns (LOOP, OSRM) and
replaces the bypass between Santa Rosa and Correo. The video says so.

    python experiments/route66_towns/line.py          # engine on (the loop) -> cache/line.json
"""

from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path

import networkx as nx
import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
ROOT = HERE.parents[1]
OSRM = "http://127.0.0.1:5002"
SKIP = {"1796334", "1796335", "17148440", "17148724"}       # IL 1926-30, IL 1930-40, MO 1936-65 (x2)
START, END = (-87.6245, 41.8794), (-118.4973, 34.0094)     # Adams & Michigan; Santa Monica Pier
# 1926-1937 New Mexico, in order (Santa Rosa .. Correo, where the loop rejoined the 1937 road)
LOOP = [(-104.682, 34.938), (-105.181, 35.535), (-105.675, 35.574), (-105.938, 35.687),
        (-106.551, 35.311), (-106.651, 35.084), (-106.690, 34.906), (-106.733, 34.806), (-107.185, 34.961)]
LOOP_NAMES = ["Santa Rosa", "Romeroville", "Pecos", "Santa Fe", "Bernalillo", "Albuquerque", "Isleta",
              "Los Lunas", "Correo"]


def km(a, b) -> float:
    return math.hypot((b[0] - a[0]) * 111.32 * math.cos(math.radians((a[1] + b[1]) / 2)), (b[1] - a[1]) * 110.57)


def main() -> None:
    rels = json.load(open(CACHE / "relations.json"))
    keep = {w for k, v in rels.items() if k not in SKIP for w in v["ways"]}
    feats = json.load(open(ROOT / "data" / "scratch" / "route66_ids.geojson", encoding="utf-8"))["features"]
    G = nx.Graph()
    for f in feats:
        i = str(f.get("id", ""))
        if not i.startswith("w") or int(i[1:]) not in keep or f["geometry"]["type"] != "LineString":
            continue
        c = [tuple(round(v, 7) for v in p) for p in f["geometry"]["coordinates"]]
        for a, b in zip(c, c[1:]):
            G.add_edge(a, b, w=km(a, b))
    nodes = np.array(G.nodes)
    def snap(p):
        return tuple(nodes[np.argmin(((nodes - p) ** 2).sum(1))])
    comps = sorted(nx.connected_components(G), key=len, reverse=True)
    print(f"{G.number_of_nodes():,} nodes, {len(comps)} pieces (largest {len(comps[0]):,})")
    s, e = snap(np.array(START)), snap(np.array(END))
    # OSM breaks the route at state lines and gaps: join the piece holding the start to the nearest
    # other piece, closest gap first, until the end is reachable. Each jump is printed.
    from scipy.spatial import cKDTree
    gaps = []
    while not nx.has_path(G, s, e):
        here = nx.node_connected_component(G, s)
        hn = np.array(list(here))
        other = np.array([n for n in G.nodes if n not in here])
        d, j = cKDTree(other).query(hn)
        i = int(d.argmin())
        a_, b_ = tuple(hn[i]), tuple(other[j[i]])
        G.add_edge(a_, b_, w=km(a_, b_))
        gaps.append(round(km(a_, b_), 2))
    print("gaps joined (km):", gaps)
    path = nx.shortest_path(G, s, e, weight="w")
    line = [list(p) for p in path]
    mi = sum(km(a, b) for a, b in zip(line, line[1:])) / 1.609344
    print(f"historic line: {len(line):,} points, {mi:,.0f} mi, start {line[0]}, end {line[-1]}")

    # swap the 1937 bypass for the Santa Fe loop
    arr = np.array(line)
    i0 = int(np.argmin(((arr - LOOP[0]) ** 2).sum(1)))
    i1 = int(np.argmin(((arr - LOOP[-1]) ** 2).sum(1)))
    assert i0 < i1
    q = ";".join(f"{x},{y}" for x, y in LOOP)
    r = json.load(urllib.request.urlopen(f"{OSRM}/route/v1/driving/{q}?overview=full&geometries=geojson"))["routes"][0]
    loop = r["geometry"]["coordinates"]
    bypass_mi = sum(km(a, b) for a, b in zip(line[i0:i1 + 1], line[i0 + 1:i1 + 1])) / 1.609344
    out = line[:i0] + loop + line[i1 + 1:]
    mi2 = sum(km(a, b) for a, b in zip(out, out[1:])) / 1.609344
    json.dump(dict(line=out, loop=[i0, i0 + len(loop) - 1], bypass=line[i0:i1 + 1], loop_names=LOOP_NAMES),
              open(CACHE / "line.json", "w"))
    print(f"Santa Fe loop {r['distance'] / 1609.344:,.0f} mi replaces the {bypass_mi:,.0f} mi bypass; total {mi2:,.0f} mi")


if __name__ == "__main__":
    main()
