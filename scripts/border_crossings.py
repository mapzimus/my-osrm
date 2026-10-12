"""Mark one node on every road or ferry that crosses a national border.

Why not barrier=border_control? Its tagging is inconsistent: at the Peace
Bridge it sits on plaza inspection lanes that the major-roads filter drops,
so 0 of 4 big crossings kept one. Instead, take the national boundary lines
(admin_level=2 relations) from the raw extracts and intersect them with the
merged routing network. profiles/car.lua charges border_crossing seconds at
each marked node.

Runs in the my-osrm-tools container:

    python border_crossings.py <merged.osm.pbf> <out.osm.pbf> <work_dir> <raw.osm.pbf>...

Writes <out> (merged + tagged nodes) and <work_dir>/border-report.json.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import osmium
from shapely import STRtree
from shapely.geometry import LineString, Point, mapping
from shapely.ops import unary_union


def meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    return math.hypot((b[0] - a[0]) * 111_320 * math.cos(lat), (b[1] - a[1]) * 110_540)

TAG = "my_osrm:border_crossing"
STRIP_DEG = 0.03          # land-border search strip (~3 km each side)
SAME_CROSSING_M = 200     # intersection points closer than this are one crossing


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


class BoundaryLines(osmium.SimpleHandler):
    """Member-way geometries of national (admin_level=2) boundary relations,
    each with the set of relations (countries) it belongs to."""

    def __init__(self, members: dict[int, set[int]]):
        super().__init__()
        self.members = members
        self.land: list[LineString] = []
        self.land_rels: list[set[int]] = []
        self.sea: list[LineString] = []
        self.sea_rels: list[set[int]] = []

    def way(self, w):
        if w.id not in self.members or len(w.nodes) < 2:
            return
        try:
            line = LineString([(n.lon, n.lat) for n in w.nodes])
        except osmium.InvalidLocationError:
            return
        if w.tags.get("maritime") == "yes":
            self.sea.append(line)
            self.sea_rels.append(self.members[w.id])
        else:
            self.land.append(line)
            self.land_rels.append(self.members[w.id])


def national_members(path: str) -> dict[int, set[int]]:
    """way id -> ids of the admin_level=2 relations it belongs to."""
    members: dict[int, set[int]] = defaultdict(set)
    for r in osmium.FileProcessor(path, osmium.osm.RELATION):
        if r.tags.get("boundary") == "administrative" and r.tags.get("admin_level") == "2":
            for m in r.members:
                if m.type == "w":
                    members[m.ref].add(r.id)
    return members


def has_line_part(geom) -> bool:
    """True if any piece of an intersection result is a line (shared stretch)."""
    if geom.is_empty:
        return False
    if hasattr(geom, "geoms"):
        return any(has_line_part(g) for g in geom.geoms)
    return isinstance(geom, LineString)


def points_of(geom) -> list[Point]:
    """Crossing points of an intersection result.

    A Point is a crossing (including a way that ENDS on the line: most
    crossings are mapped as two ways meeting at a border node, which is why
    the `crosses` predicate alone loses 40% of them). A line part is a
    stretch shared with the boundary (Canusa St, Derby Line) and is not a
    crossing at all.
    """
    if geom.is_empty:
        return []
    if isinstance(geom, Point):
        return [geom]
    if hasattr(geom, "geoms"):
        return [p for g in geom.geoms for p in points_of(g)]
    return []


class Crossings(osmium.SimpleHandler):
    """Ways that cross a boundary line -> one tagged node per crossing.

    Roads: point intersections only, so a street running along the line
    (Canusa St in Derby Line) is not charged; every distinct crossing point
    on a way is tagged (11 Central-American roads cross twice).

    Ferries: a country's own 12-nm territorial-sea limit is a member of its
    admin_level=2 relation, so any ferry leaving coastal waters crossed "a
    border". Customs only happens when the boat changes country: tag only if
    some relation's line is crossed an odd number of times, and then once.
    """

    def __init__(self, lines: list[LineString], rels: list[set[int]], ferries_only: bool):
        super().__init__()
        self.lines = lines
        self.rels = rels
        self.tree = STRtree(lines)
        self.ferries_only = ferries_only
        self.nodes: dict[int, dict] = {}

    def way(self, w):
        is_ferry = w.tags.get("route") == "ferry"
        if self.ferries_only != is_ferry or (not is_ferry and "highway" not in w.tags):
            return
        try:
            pts = [(n.ref, n.lon, n.lat) for n in w.nodes]
        except osmium.InvalidLocationError:
            return
        if len(pts) < 2:
            return
        road = LineString([(x, y) for _, x, y in pts])
        hits: list[tuple[Point, set[int]]] = []
        for i in self.tree.query(road, predicate="intersects"):
            hit = road.intersection(self.lines[i])
            if has_line_part(hit):
                continue          # runs along the border (Canusa St): no crossing
            for p in points_of(hit):
                for q, rels in hits:                 # same crossing, shared way
                    if meters((p.x, p.y), (q.x, q.y)) < SAME_CROSSING_M:
                        rels |= self.rels[i]
                        break
                else:
                    hits.append((p, set(self.rels[i])))
        if not hits:
            return
        if is_ferry:
            count: dict[int, int] = defaultdict(int)
            for _, rels in hits:
                for r in rels:
                    count[r] += 1
            if not any(c % 2 for c in count.values()):
                return                               # left and came back: domestic
            hits = hits[:1]                          # one customs stop per boat
        for p, _ in hits:
            ref = min(pts, key=lambda t: (t[1] - p.x) ** 2 + (t[2] - p.y) ** 2)[0]
            self.nodes.setdefault(ref, {"way": w.id, "name": w.tags.get("name", ""),
                                        "ferry": is_ferry, "lon": p.x, "lat": p.y})


def main(merged: str, out: str, work: str, *raws: str) -> None:
    wd = Path(work)
    wd.mkdir(parents=True, exist_ok=True)

    # 1. National boundary relations (+ member ways/nodes) from each raw extract.
    parts = []
    for raw in raws:
        part = wd / f"boundaries-{Path(raw).name.split('-latest')[0]}.osm.pbf"
        if not part.exists():
            # admin_level=2 only: r/boundary=administrative also pulled every
            # county and township line, which took 21 min on the US extract.
            # Written under a temp name and renamed when complete: a machine
            # freeze mid-write left a truncated file that a resumed run trusted.
            tmp = part.with_suffix(".part.pbf")
            run("osmium", "tags-filter", "--overwrite", "-F", "pbf", "-f", "pbf",
                "-o", str(tmp), raw, "r/admin_level=2")
            tmp.replace(part)
        parts.append(str(part))
    bounds = wd / "boundaries.osm.pbf"
    run("osmium", "merge", "--overwrite", "-o", str(bounds), *parts)
    members = national_members(str(bounds))
    lines = BoundaryLines(members)
    lines.apply_file(str(bounds), locations=True)
    print(f"boundary ways: {len(lines.land)} land, {len(lines.sea)} maritime", flush=True)

    # 2. Roads near land borders (thin strip cut from the merged network).
    strip = unary_union([ln.buffer(STRIP_DEG) for ln in lines.land]).simplify(0.005)
    (wd / "border-strip.geojson").write_text(json.dumps(
        {"type": "Feature", "properties": {}, "geometry": mapping(strip)}))
    near = wd / "near-borders.osm.pbf"
    run("osmium", "extract", "--overwrite", "-p", str(wd / "border-strip.geojson"),
        "-o", str(near), merged)
    roads = Crossings(lines.land, lines.land_rels, ferries_only=False)
    roads.apply_file(str(near), locations=True)

    # 3. Ferries against every boundary, maritime included (customs on arrival).
    ferries_pbf = wd / "ferries.osm.pbf"
    run("osmium", "tags-filter", "--overwrite", "-o", str(ferries_pbf), merged, "w/route=ferry")
    ferries = Crossings(lines.land + lines.sea, lines.land_rels + lines.sea_rels, ferries_only=True)
    ferries.apply_file(str(ferries_pbf), locations=True)

    marked = {**roads.nodes, **ferries.nodes}
    print(f"crossings: {len(roads.nodes)} roads, {len(ferries.nodes)} ferries", flush=True)

    # 4. Tag those nodes: extract them, bump version + add the tag, apply as a change.
    ids = wd / "crossing-nodes.txt"
    ids.write_text("".join(f"n{i}\n" for i in sorted(marked)))
    orig = wd / "crossing-nodes.osm.pbf"
    run("osmium", "getid", "--overwrite", "-i", str(ids), "-o", str(orig), merged)
    patch = wd / "crossing-nodes.osc"
    patch.unlink(missing_ok=True)
    with osmium.SimpleWriter(str(patch)) as writer:
        for n in osmium.FileProcessor(str(orig), osmium.osm.NODE):
            tags = {k: v for k, v in n.tags}
            tags[TAG] = "yes"
            writer.add_node(n.replace(tags=tags, version=n.version + 1))
    run("osmium", "apply-changes", "--overwrite", "-o", out, merged, str(patch))

    (wd / "border-report.json").write_text(json.dumps(
        [{"node": k, **v} for k, v in sorted(marked.items())], indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
