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
# Docks whose last hop is a footway/path/pier (369 of 1,156 kept ferries in
# Oct 2026). Walked at a heavy penalty and only when short, then written out
# as highway=service so OSRM will drive them.
WALK = {"footway", "path", "pedestrian"}
WALK_MAX_M = 300
WALK_PENALTY = 6.0
BOX_DEG = 0.06          # search area around each dock (~6 km)
NO = {"no", "private"}
# Ferry staging lanes are often tagged access=private ("ticket holders"),
# which OSRM's car profile refuses. Allow them as dock connectors at a cost
# penalty (public roads win when they exist), then strip the tag on output.
GATED = {"private", "destination", "customers", "permit"}
GATED_PENALTY = 3.0
# A major-road node only counts as "the network" if this much major road lies
# behind it (to drive IN to the dock) or ahead of it (to drive OUT), following
# oneway rules. Tsawwassen: the ticket-booth lanes are highway=service, so the
# major filter left the inbound Ferry Causeway as a stranded 1 km trunk
# fragment; the berth search stopped there, and the terminal was exit-only.
MIN_MAJOR_REACH_M = 3_000
# Docks reachable one way only: the ferry line ends at the tip of a oneway
# boarding lane and the exit lane is not attached to it (Walpole Island:
# cars could board but never drive off). Retry the failed direction letting
# oneway service lanes run backwards at this cost factor; the lanes used are
# written out as two-way (my_osrm:dock_contraflow=yes).
CONTRA_PENALTY = 4.0



def ready(path: Path) -> bool:
    """Exists AND non-empty: a crashed run left 0-byte files that looked done."""
    return path.exists() and path.stat().st_size > 0

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
        self.ferry_ways_at: dict[int, int] = defaultdict(int)

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
        # Long crossings are split into several ferry ways; their shared
        # joints touch no road and must not be mistaken for stranded docks.
        for ref in {n.ref for n in w.nodes}:
            self.ferry_ways_at[ref] += 1


Adjacency = dict[int, list[tuple[int, float, int]]]


def oneway(tags) -> int:
    """+1 forward only, -1 reverse only, 0 both ways (OSM oneway rules)."""
    ow = tags.get("oneway")
    if ow in {"yes", "true", "1"}:
        return 1
    if ow == "-1":
        return -1
    if ow == "no":
        return 0
    if tags.get("junction") in {"roundabout", "circular"} or tags.get("highway") == "motorway":
        return 1
    return 0


class RoadGraph(osmium.SimpleHandler):
    """Directed node graph of car-routable roads near the docks.

    `out` follows legal driving direction; `inn` is its reverse, so a search
    on `inn` from a dock finds the way IN to it. A dock needs both: an
    undirected search kept only one lane per dock, so Cross Sound cars could
    board in Bridgeport but never leave the dock in Port Jefferson.
    """

    def __init__(self) -> None:
        super().__init__()
        self.out: Adjacency = defaultdict(list)
        self.inn: Adjacency = defaultdict(list)
        self.major_nodes: set[int] = set()
        # Major roads only, same direction convention as out/inn.
        self.major_out: Adjacency = defaultdict(list)
        self.major_inn: Adjacency = defaultdict(list)
        self.gated_ways: set[int] = set()
        self.walk_ways: set[int] = set()
        self.reach_out: dict[int, bool] = {}
        self.reach_in: dict[int, bool] = {}
        # Oneway service lanes, reversed (for the one-way-dock retry).
        self.contra_out: Adjacency = defaultdict(list)
        self.contra_inn: Adjacency = defaultdict(list)
        self.contra_ways: set[int] = set()
        self.two_way: set[int] = set()        # lanes to write out as two-way

    def way(self, w) -> None:
        hw = w.tags.get("highway")
        walk = hw in WALK or w.tags.get("man_made") == "pier"
        if hw not in MAJOR and hw not in MINOR and not walk:
            return
        access = {w.tags.get(k) for k in ("access", "vehicle", "motor_vehicle", "motorcar")}
        if "no" in access and not walk:          # a pier's motor_vehicle=no is expected
            return
        refs = [(n.ref, (n.lon, n.lat)) for n in w.nodes]
        if walk:
            length = sum(meters(a, b) for (_, a), (_, b) in zip(refs, refs[1:]))
            if length > WALK_MAX_M:
                return
            self.walk_ways.add(w.id)
        gated = bool(access & GATED)
        if gated:
            self.gated_ways.add(w.id)
        if hw in MAJOR and not gated:
            self.major_nodes.update(r for r, _ in refs)
        direction = 0 if walk else oneway(w.tags)
        factor = WALK_PENALTY if walk else (GATED_PENALTY if gated else 1.0)
        is_major = hw in MAJOR and not gated
        contra = hw == "service" and direction != 0
        if contra:
            self.contra_ways.add(w.id)
        for (a, pa), (b, pb) in zip(refs, refs[1:]):
            d = meters(pa, pb) * factor
            if contra:   # the direction the oneway forbids
                x, y = (b, a) if direction > 0 else (a, b)
                self.contra_out[x].append((y, d * CONTRA_PENALTY, w.id))
                self.contra_inn[y].append((x, d * CONTRA_PENALTY, w.id))
            if direction >= 0:
                self.out[a].append((b, d, w.id))
                self.inn[b].append((a, d, w.id))
                if is_major:
                    self.major_out[a].append((b, d, w.id))
                    self.major_inn[b].append((a, d, w.id))
            if direction <= 0:
                self.out[b].append((a, d, w.id))
                self.inn[a].append((b, d, w.id))
                if is_major:
                    self.major_out[b].append((a, d, w.id))
                    self.major_inn[a].append((b, d, w.id))


def has_reach(adj: Adjacency, node: int, cache: dict[int, bool]) -> bool:
    """True if MIN_MAJOR_REACH_M of road, in total, is reachable from node.

    Total length, not distance: a dock at the tip of a short peninsula
    (Greenport, Jamestown VA) is never 3 km from anything, yet sits on the
    real network; a stranded fragment is short no matter how it is measured.
    """
    if node in cache:
        return cache[node]
    seen = {node}
    stack = [node]
    total = 0.0
    ok = False
    while stack and not ok:
        n = stack.pop()
        for nxt, step, _ in adj[n]:
            if nxt not in seen:
                seen.add(nxt)
                total += step
                if total >= MIN_MAJOR_REACH_M:
                    ok = True
                    break
                stack.append(nxt)
    cache[node] = ok
    return ok


def search(adj: Adjacency, is_target, start: int) -> tuple[bool, float, set[int]]:
    """Dijkstra to the nearest node that is_target accepts.

    Returns (reached, meters, ways): the path's ways if reached, otherwise
    every way explored (the whole local minor-road network).
    """
    dist = {start: 0.0}
    prev: dict[int, tuple[int, int]] = {}
    heap = [(0.0, start)]
    while heap:
        d, node = heapq.heappop(heap)
        if d > dist[node]:
            continue
        if is_target(node):
            ways = set()
            while node != start:
                node, way_id = prev[node]
                ways.add(way_id)
            return True, d, ways
        for nxt, step, way_id in adj[node]:
            nd = d + step
            if nd < dist.get(nxt, math.inf):
                dist[nxt] = nd
                prev[nxt] = (node, way_id)
                heapq.heappush(heap, (nd, nxt))
    return False, 0.0, {way_id for _, way_id in prev.values()}


class Joined:
    """Two adjacency maps read as one (search only indexes it)."""

    def __init__(self, a: Adjacency, b: Adjacency) -> None:
        self.a, self.b = a, b

    def __getitem__(self, n: int) -> list:
        return self.a[n] + self.b[n]


def connector(graph: RoadGraph, start: int) -> tuple[str, float, list[int]]:
    """Keep a legal way OUT of the dock and a legal way IN to it."""
    def leads_on(n: int) -> bool:      # out-search target: major road ahead
        return n in graph.major_nodes and has_reach(graph.major_out, n, graph.reach_out)

    def fed_from(n: int) -> bool:      # in-search target: major road behind
        return n in graph.major_nodes and has_reach(graph.major_inn, n, graph.reach_in)

    if leads_on(start) and fed_from(start):
        return "on_major", 0.0, []
    if start not in graph.out and start not in graph.inn:
        return "floating", 0.0, []  # no road touches this ferry end
    got_out, m_out, ways_out = search(graph.out, leads_on, start)
    got_in, m_in, ways_in = search(graph.inn, fed_from, start)
    status = "connected"
    if got_out != got_in:
        # One-way dock: retry the failed direction with reversible lanes.
        adj, extra, target = ((graph.out, graph.contra_out, leads_on) if not got_out
                              else (graph.inn, graph.contra_inn, fed_from))
        ok, m, retry_ways = search(Joined(adj, extra), target, start)
        if ok:
            graph.two_way.update(retry_ways & graph.contra_ways)
            if not got_out:
                got_out, m_out, ways_out = ok, m, retry_ways
            else:
                got_in, m_in, ways_in = ok, m, retry_ways
            status = "connected_contraflow"
    ways = sorted(ways_out | ways_in)
    if got_out and got_in:
        return status, max(m_out, m_in), ways
    if got_out or got_in:
        return "one_way_only", max(m_out, m_in), ways
    # No major road in reach: an island (or isolated shore) served only by
    # minor roads. Those roads ARE the destination, so keep all of them.
    return "island", 0.0, ways


def ungate(src: Path, dst: Path, gated_ids: set[int], walk_ids: set[int],
           two_way_ids: set[int] = frozenset()) -> int:
    """Copy src to dst: drop gate tags on gated ways, make walk ways drivable.

    Every rewritten way gets version+1. The same way (same id, same version)
    also exists in the major-roads file; with equal versions `osmium merge`
    kept one copy at random, so Cape May-Lewes Ferry Road came out gated in
    27 of 49 cases. A higher version makes the dedup keep ours, every time.
    """
    dst.unlink(missing_ok=True)
    changed = 0
    with osmium.SimpleWriter(str(dst)) as writer:
        for obj in osmium.FileProcessor(str(src)):
            if obj.is_way() and (obj.id in gated_ids or obj.id in walk_ids
                                 or obj.id in two_way_ids):
                tags = {k: v for k, v in obj.tags
                        if not (k in {"access", "vehicle", "motor_vehicle", "motorcar"}
                                and (v in GATED or (obj.id in walk_ids and v == "no")))}
                if obj.id in walk_ids:
                    tags["highway"] = "service"
                    tags["my_osrm:dock_walkway"] = "yes"
                if obj.id in two_way_ids:
                    tags["oneway"] = "no"
                    tags["my_osrm:dock_contraflow"] = "yes"
                writer.add_way(obj.replace(tags=tags, version=obj.version + 1))
                changed += 1
            else:
                writer.add(obj)
    return changed


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
    #    Steps 1-2 each scan the full extract; skip them on re-runs.
    #    Temp name + rename: a freeze mid-write must not leave a truncated
    #    file that a resumed run trusts.
    if not ready(ferries_pbf):
        tmp = ferries_pbf.with_suffix(".part.pbf")
        run("osmium", "tags-filter", "--overwrite", "-F", "pbf", "-f", "pbf",
            "-o", str(tmp), raw, "w/route=ferry")
        tmp.replace(ferries_pbf)
    ferries = Ferries()
    ferries.apply_file(str(ferries_pbf), locations=True)
    print(f"car ferries: {len(ferries.ways)}", flush=True)
    if not ferries.ways:
        # Nothing to connect (a cut-out, or a landlocked extract): an empty
        # union would crash osmium extract. Emit an empty result instead.
        run("osmium", "cat", "--overwrite", "-o", str(result_pbf), str(ferries_pbf))
        (out / "ferry-report.json").write_text(json.dumps(
            {"summary": {}, "connector_ways": 0, "endpoints": []}, indent=1))
        return

    # 2. Cut a small area around every dock out of the full extract.
    docks = {e for f in ferries.ways.values() for e in f["ends"]}
    area = unary_union([
        box(lon - BOX_DEG, lat - BOX_DEG, lon + BOX_DEG, lat + BOX_DEG)
        for _, lon, lat in docks
    ])
    boxes_json.write_text(json.dumps(
        {"type": "Feature", "properties": {}, "geometry": mapping(area)}
    ))
    if not ready(near_pbf):
        tmp = near_pbf.with_suffix(".part.pbf")
        run("osmium", "extract", "--overwrite", "-F", "pbf", "-f", "pbf", "-p", str(boxes_json),
            "-o", str(tmp), raw)
        tmp.replace(near_pbf)

    # 3. Shortest minor-road path from each dock to the major network.
    graph = RoadGraph()
    graph.apply_file(str(near_pbf), locations=True)
    keep: set[int] = set()
    report = []
    for fid, f in ferries.ways.items():
        for ref, lon, lat in f["ends"]:
            status, meters_, ways = connector(graph, ref)
            if status == "floating" and ferries.ferry_ways_at[ref] > 1:
                status = "ferry_joint"
            keep.update(ways)
            report.append({
                "ferry_way": fid, "name": f["name"], "node": ref,
                "lon": lon, "lat": lat, "status": status,
                "connector_m": round(meters_), "connector_ways": len(ways),
            })

    # 4. Ferry ways + connector ways (with their nodes) -> one PBF.
    if keep:
        ids_txt.write_text("".join(f"w{i}\n" for i in sorted(keep)))
        raw_connectors = out / "connector-ways-raw.osm.pbf"
        run("osmium", "getid", "--overwrite", "-r", "-i", str(ids_txt),
            "-o", str(raw_connectors), str(near_pbf))
        ungated = ungate(raw_connectors, connectors_pbf, keep & graph.gated_ways,
                         keep & graph.walk_ways, keep & graph.two_way)
        print(f"ungated connector ways: {ungated}", flush=True)
        run("osmium", "merge", "--overwrite", "-o", str(result_pbf),
            str(ferries_pbf), str(connectors_pbf))
    else:
        # Every dock already sits on a major road (Bermuda): osmium getid
        # rejects an empty id list, and the ferries alone are the result.
        print("no connector ways needed", flush=True)
        run("osmium", "cat", "--overwrite", "-o", str(result_pbf), str(ferries_pbf))

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
