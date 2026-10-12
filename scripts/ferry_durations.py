"""Fix ferry duration tags that OSRM would read wrong.

OSRM's car profile charges a ferry way's `duration` tag in full, per way. Two
OSM tagging patterns turn that into hours of phantom delay (Oct 2026 scan of
the 404 timed car ferries):

1. Copied durations on split crossings. A crossing drawn as a long line plus
   short approach pieces, with every piece tagged the whole crossing time.
   Seattle-Bremerton's 233 m approach stub says duration=01:00, so the trip
   paid the hour twice. Same on Jamestown-Scotland (4 pieces x 00:20) and
   Ile d'Entree - Cap-aux-Meules. Rule (stub_ways): a ferry way that touches
   a same-name ferry way at least STUB_RATIO times longer, with the same
   duration, and not the same pair of endpoints (parallel two-direction lines
   like South Ferry or DeTour-Drummond keep theirs), loses its duration tag.
   It then runs at the profile's untimed ferry speed: seconds, not an hour.

2. Minutes written as hours. "60:00" on Kingstown-Bequia, "30:00" on Daniel
   Island-Charleston and on Qaarsut-Uummannaq (whose own name says "30 min"),
   "05:00" on a 300 m river crossing. OSRM reads HH:MM, so these are 60, 30
   and 5 HOURS and the ferry is never used. Rule (mmss_fix): an HH:MM value
   with HH >= MIN_HH whose implied speed is under 1 km/h, but reads as a
   plausible 3-80 km/h when taken as MM:SS, is rewritten to that many minutes.
   "01:00" is left alone: too ambiguous.

Runs in the my-osrm-tools container, between car_ferries.py and osrm-extract:

    python ferry_durations.py <in.osm.pbf> <out.osm.pbf> <work_dir>

Writes <work_dir>/ferry-durations-report.json listing every change.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

STUB_RATIO = 4          # sibling must be at least this many times longer
STUB_MAX_M = 1_000      # approach stubs are short; never strip a real crossing
MIN_HH = 5              # "05:00" and up can be MM:SS typos; "01:00" can't tell
SLOW_KMH = 1.0          # implied speed below this is implausible for a ferry
PLAUSIBLE_KMH = (3.0, 80.0)


def parse_duration(value: str | None) -> int | None:
    """Seconds, the way OSRM reads it: HH:MM, HH:MM:SS, bare minutes, ISO 8601."""
    if not value:
        return None
    v = value.strip()
    m = re.fullmatch(r"P(?:T)?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v, re.I)
    if m and any(m.groups()):
        h, mi, s = (int(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + s
    parts = v.split(":")
    if not all(p.isdigit() for p in parts):
        return None
    p = [int(x) for x in parts]
    if len(p) == 1:
        return p[0] * 60
    if len(p) == 2:
        return p[0] * 3600 + p[1] * 60
    if len(p) == 3:
        return p[0] * 3600 + p[1] * 60 + p[2]
    return None


def kmh(length_m: float, seconds: int | None) -> float | None:
    return None if not seconds else length_m / seconds * 3.6


def mmss_fix(value: str | None, length_m: float) -> str | None:
    """New duration for an HH:MM value that is really MM:SS, else None."""
    if not value or not re.fullmatch(r"\d+:\d{2}", value.strip()):
        return None
    hh, mm = (int(x) for x in value.strip().split(":"))
    speed = kmh(length_m, parse_duration(value))
    if hh < MIN_HH or speed is None or speed >= SLOW_KMH:
        return None
    as_minutes = hh * 60 + mm                      # seconds, read as MM:SS
    lo, hi = PLAUSIBLE_KMH
    if not lo <= length_m / as_minutes * 3.6 <= hi:
        return None
    return f"{hh // 60:02d}:{hh % 60:02d}:{mm:02d}"


@dataclass
class FerryWay:
    id: int
    name: str
    duration: str | None
    length_m: float
    nodes: set[int] = field(default_factory=set)
    ends: frozenset[int] = frozenset()


def stub_ways(ferries: dict[int, FerryWay]) -> dict[int, int]:
    """{stub way id: sibling way id} for approach pieces copying the crossing's duration."""
    byname: dict[str, list[FerryWay]] = defaultdict(list)
    for f in ferries.values():
        if f.name:
            byname[f.name].append(f)
    stubs: dict[int, int] = {}
    for group in byname.values():
        if len(group) < 2:
            continue
        for a in group:
            da = parse_duration(a.duration)
            if not da or a.length_m >= STUB_MAX_M:
                continue
            for b in group:
                if (b.id != a.id and a.nodes & b.nodes and a.ends != b.ends
                        and b.length_m >= STUB_RATIO * a.length_m
                        and parse_duration(b.duration) == da):
                    stubs[a.id] = b.id
                    break
    return stubs


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6_371_000.0
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    dlat, dlon = la2 - la1, math.radians(b[0] - a[0])
    h = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def plan(ferries: dict[int, FerryWay]) -> dict[int, dict]:
    """{way id: change} with change = {"duration": new value or None, "why": ...}."""
    changes: dict[int, dict] = {}
    for stub, sibling in stub_ways(ferries).items():
        f = ferries[stub]
        changes[stub] = {"name": f.name, "length_m": round(f.length_m), "old": f.duration, "duration": None,
                         "why": f"copies the duration of w{sibling} ({ferries[sibling].length_m:.0f} m)"}
    for f in ferries.values():
        if f.id in changes:
            continue
        new = mmss_fix(f.duration, f.length_m)
        if new:
            changes[f.id] = {"name": f.name, "length_m": round(f.length_m), "old": f.duration, "duration": new,
                             "why": f"{f.duration} read as hours is {kmh(f.length_m, parse_duration(f.duration)):.2f} km/h"}
    return changes


def main(src: str, dst: str, work_dir: str) -> None:
    import osmium

    ferries: dict[int, FerryWay] = {}

    class Collect(osmium.SimpleHandler):
        def way(self, w):
            if w.tags.get("route") != "ferry":
                return
            try:
                pts = [(n.lon, n.lat) for n in w.nodes]
            except osmium.InvalidLocationError:
                return
            refs = [n.ref for n in w.nodes]
            ferries[w.id] = FerryWay(
                id=w.id, name=w.tags.get("name", ""), duration=w.tags.get("duration"),
                length_m=sum(haversine(a, b) for a, b in zip(pts, pts[1:])),
                nodes=set(refs), ends=frozenset((refs[0], refs[-1])))

    Collect().apply_file(src, locations=True, idx="flex_mem")
    changes = plan(ferries)

    out = Path(dst)
    tmp = out.with_suffix(".part.pbf")
    tmp.unlink(missing_ok=True)
    with osmium.SimpleWriter(str(tmp)) as writer:
        for obj in osmium.FileProcessor(src):
            if obj.is_way() and obj.id in changes:
                tags = {k: v for k, v in obj.tags if k != "duration"}
                new = changes[obj.id]["duration"]
                if new:
                    tags["duration"] = new
                tags["my_osrm:duration_fix"] = "stub" if new is None else "mmss"
                writer.add_way(obj.replace(tags=tags))
            else:
                writer.add(obj)
    tmp.replace(out)

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    (work / "ferry-durations-report.json").write_text(json.dumps(
        {"ferry_ways": len(ferries), "timed": sum(1 for f in ferries.values() if f.duration),
         "changed": {str(k): v for k, v in sorted(changes.items())}}, indent=1, ensure_ascii=False))
    stubs = sum(1 for c in changes.values() if c["duration"] is None)
    print(f"ferry durations: {len(ferries)} ferry ways, {stubs} copied-duration stubs cleared, "
          f"{len(changes) - stubs} MM:SS typos fixed", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
