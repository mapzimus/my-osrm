"""Extract car ferries plus the minimal road connectors that reach them.

Why: the major-roads filter (motorway..tertiary) strips the service and
residential roads that link ferry docks to the highway network, so ferries
survive the filter as disconnected islands the router can never reach.
Keeping all minor roads blows past BRONTOSAURUS's memory ceiling. Instead,
from each ferry endpoint, find the shortest path over minor roads to the
nearest major road and keep only the ways on that path.

Runs inside the my-osrm-tools container (osmium CLI + pyosmium + shapely):

    python ferry_connectors.py /data/us-latest.osm.pbf /data/build/us

Writes <out_dir>/ferry-connectors.osm.pbf (ferry ways + connector ways)
and <out_dir>/ferry-report.json (per-endpoint outcome, for auditing).
"""

from __future__ import annotations

import heapq
import json
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import osmium
from shapely.geometry import box, mapping
from shapely.ops import unary_union

MAJOR = {
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}
MINOR = {"unclassified", "residential", "service", "living_street", "road"}
BOX_DEG = 0.06          # search area around each dock (~6 km)
NO = {"no", "private"}


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    dx = (b[0] - a[0]) * 111_320 * math.cos(lat)
    dy = (b[1] - a[1]) * 110_540
    return math.hypot(dx, dy)


class Ferries(osmium.SimpleHandler):
    """Car-usable ferry ways and their endpoint nodes."""

    def __init__(self) -> None:
        super().__init__()
        self.ways: dict[int, dict] = {}

    def way(self, w) -> None:
        t = w.tags
        if t.get("route") != "ferry":
            return
        if t.get("motor_vehicle") in NO or t.get("motorcar") in NO:
            return
        if len(w.nodes) < 2:
            return
        ends = [w.nodes[0], w.nodes[-1]]
        self.ways[w.id] = {
            "name": t.get("name", ""),
            "ends": [(n.ref, n.lon, n.lat) for n in ends],
        }


class RoadGraph(osmium.SimpleHandler):
    """Undirected node graph of car-routable roads near the docks."""

    def __init__(self) -> None:
        super().__init__()
        self.adj: dict[int, list[tuple[int, float, int]]] = defaultdict(list)
        self.major_nodes: set[int] = set()

    def way(self, w) -> None:
        hw = w.tags.get("highway")
        if hw not in MAJOR and hw not in MINOR:
            return
        if w.tags.get("access") in NO or w.tags.get("motor_vehicle") in NO:
            return
        refs = [(n.ref, (n.lon, n.lat)) for n in w.nodes]
        if hw in MAJOR:
            self.major_nodes.update(r for r, _ in refs)
        for (a, pa), (b, pb) in zip(refs, refs[1:]):
            d = meters(pa, pb)
            self.adj[a].append((b, d, w.id))
            self.adj[b].append((a, d, w.id))


def connector(graph: RoadGraph, start: int) -> tuple[str, float, list[int]]:
    """Dijkstra from a dock node to the nearest major-road node."""
    if start in graph.major_nodes:
        return "on_major", 0.0, []
    if start not in graph.adj:
        return "floating", 0.0, []  # no road touches this ferry end
    dist = {start: 0.0}
    prev: dict[int, tuple[int, int]] = {}
    heap = [(0.0, start)]
    while heap:
        d, node = heapq.heappop(heap)
        if d > dist[node]:
            continue
        if node in graph.major_nodes:
            ways = []
            while node != start:
                node, way_id = prev[node]
                ways.append(way_id)
            return "connected", d, sorted(set(ways))
        for nxt, step, way_id in graph.adj[node]:
            nd = d + step
            if nd < dist.get(nxt, math.inf):
                dist[nxt] = nd
                prev[nxt] = (node, way_id)
                heapq.heappush(heap, (nd, nxt))
    return "unreached", 0.0, []


def main(raw: str, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ferries_pbf = out / "ferries.osm.pbf"
    near_pbf = out / "near-docks.osm.pbf"
    boxes_json = out / "dock-boxes.geojson"
    ids_txt = out / "connector-ids.txt"
    connectors_pbf = out / "connector-ways.osm.pbf"
    result_pbf = out / "ferry-connectors.osm.pbf"

    # 1. All ferry ways (+ their nodes, so endpoints have locations).
    run("osmium", "tags-filter", "--overwrite", "-o", str(ferries_pbf), raw, "w/route=ferry")
    ferries = Ferries()
    ferries.apply_file(str(ferries_pbf), locations=True)
    print(f"car ferries: {len(ferries.ways)}", flush=True)

    # 2. Cut a small area around every dock out of the full extract.
    docks = {e for f in ferries.ways.values() for e in f["ends"]}
    area = unary_union([
        box(lon - BOX_DEG, lat - BOX_DEG, lon + BOX_DEG, lat + BOX_DEG)
        for _, lon, lat in docks
    ])
    boxes_json.write_text(json.dumps(
        {"type": "Feature", "properties": {}, "geometry": mapping(area)}
    ))
    run("osmium", "extract", "--overwrite", "-p", str(boxes_json),
        "-o", str(near_pbf), raw)

    # 3. Shortest minor-road path from each dock to the major network.
    graph = RoadGraph()
    graph.apply_file(str(near_pbf), locations=True)
    keep: set[int] = set()
    report = []
    for fid, f in ferries.ways.items():
        for ref, lon, lat in f["ends"]:
            status, meters_, ways = connector(graph, ref)
            keep.update(ways)
            report.append({
                "ferry_way": fid, "name": f["name"], "node": ref,
                "lon": lon, "lat": lat, "status": status,
                "connector_m": round(meters_), "connector_ways": len(ways),
            })

    # 4. Ferry ways + connector ways (with their nodes) -> one PBF.
    ids_txt.write_text("".join(f"w{i}\n" for i in sorted(keep)))
    run("osmium", "getid", "--overwrite", "-r", "-i", str(ids_txt),
        "-o", str(connectors_pbf), str(near_pbf))
    run("osmium", "merge", "--overwrite", "-o", str(result_pbf),
        str(ferries_pbf), str(connectors_pbf))

    counts: dict[str, int] = defaultdict(int)
    for r in report:
        counts[r["status"]] += 1
    (out / "ferry-report.json").write_text(json.dumps(
        {"summary": dict(counts), "connector_ways": len(keep), "endpoints": report},
        indent=1,
    ))
    print(f"endpoints: {dict(counts)}  connector ways kept: {len(keep)}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
