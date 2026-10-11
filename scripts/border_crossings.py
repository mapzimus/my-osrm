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
import subprocess
import sys
from pathlib import Path

import osmium
from shapely import STRtree
from shapely.geometry import LineString, Point, mapping
from shapely.ops import unary_union

TAG = "my_osrm:border_crossing"
STRIP_DEG = 0.03          # land-border search strip (~3 km each side)


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


class BoundaryLines(osmium.SimpleHandler):
    """Member-way geometries of national (admin_level=2) boundary relations."""

    def __init__(self, member_ids: set[int]):
        super().__init__()
        self.member_ids = member_ids
        self.land: list[LineString] = []
        self.sea: list[LineString] = []

    def way(self, w):
        if w.id not in self.member_ids or len(w.nodes) < 2:
            return
        try:
            line = LineString([(n.lon, n.lat) for n in w.nodes])
        except osmium.InvalidLocationError:
            return
        (self.sea if w.tags.get("maritime") == "yes" else self.land).append(line)


def national_members(path: str) -> set[int]:
    ids: set[int] = set()
    for r in osmium.FileProcessor(path, osmium.osm.RELATION):
        if r.tags.get("boundary") == "administrative" and r.tags.get("admin_level") == "2":
            ids.update(m.ref for m in r.members if m.type == "w")
    return ids


class Crossings(osmium.SimpleHandler):
    """Ways that intersect a boundary line -> the node nearest the crossing."""

    def __init__(self, lines: list[LineString], ferries_only: bool):
        super().__init__()
        self.lines = lines
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
        for i in self.tree.query(road, predicate="intersects"):
            hit = road.intersection(self.lines[i])
            p = hit if isinstance(hit, Point) else hit.representative_point()
            ref = min(pts, key=lambda t: (t[1] - p.x) ** 2 + (t[2] - p.y) ** 2)[0]
            self.nodes.setdefault(ref, {"way": w.id, "name": w.tags.get("name", ""),
                                        "ferry": is_ferry, "lon": p.x, "lat": p.y})
            break  # one customs stop per way, even if it weaves along the line


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
            run("osmium", "tags-filter", "-o", str(part), raw, "r/admin_level=2")
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
    roads = Crossings(lines.land, ferries_only=False)
    roads.apply_file(str(near), locations=True)

    # 3. Ferries against every boundary, maritime included (customs on arrival).
    ferries_pbf = wd / "ferries.osm.pbf"
    run("osmium", "tags-filter", "--overwrite", "-o", str(ferries_pbf), merged, "w/route=ferry")
    ferries = Crossings(lines.land + lines.sea, ferries_only=True)
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
