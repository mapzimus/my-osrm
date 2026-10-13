"""Ferry boats for the camera video: which crossings the wave uses, and when.

Geometry: data/app/ferries.geojson (kind=ferry LineStrings, built from the
engine's own na-ferry.osm.pbf by app/setup/ferries.py).

A ferry counts as *used* when its far end lights up at least 60% of a
crossing later than its near end -- i.e. the boat, not a road around, is what
reached it. The boat sails during the last `crossing` hours before the far
end lights (the 30-min boarding wait sits before that, at the dock).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

UNTIMED_KMH = 25.0          # profiles/car.lua route_speeds.ferry for untimed ferries
USED_FRACTION = 0.6
MIN_SAIL_H = 0.6            # visual floor: a 10-min hop still gets a few frames
GEOJSON = Path(__file__).resolve().parents[2] / "data" / "app" / "ferries.geojson"


@dataclass
class Ferry:
    name: str
    lonlat: np.ndarray        # (N, 2), departure end first
    t0: float                 # sailing starts (hours from origin)
    t1: float                 # far end lights
    crossing: float           # hours

    @property
    def t1_vis(self) -> float:
        return max(self.t1, self.t0 + MIN_SAIL_H)


def parse_duration(s: str | None) -> float | None:
    """OSM duration 'HH:MM', 'HH:MM:SS' or 'MM' -> hours."""
    if not s:
        return None
    parts = [p for p in re.split(r"[:.]", s.strip()) if p]
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 1:
        return nums[0] / 60
    h, m = nums[0], nums[1]
    sec = nums[2] if len(nums) > 2 else 0.0
    return h + m / 60 + sec / 3600


def length_km(coords) -> float:
    total = 0.0
    for (a, b), (c, d) in zip(coords, coords[1:]):
        lat = math.radians((b + d) / 2)
        total += math.hypot((c - a) * 111.32 * math.cos(lat), (d - b) * 110.54)
    return total


def classify(ta: float | None, tb: float | None, crossing: float):
    """(t0, t1, reversed) for a used ferry, else None. ta/tb: hours at each end."""
    if ta is None or tb is None or not (math.isfinite(ta) and math.isfinite(tb)):
        return None
    rev = tb < ta
    near, far = (tb, ta) if rev else (ta, tb)
    if far - near < USED_FRACTION * crossing:
        return None                      # land reached the far side first
    return max(near, far - crossing), far, rev


def _key(pt) -> tuple[float, float]:
    return round(pt[0], 5), round(pt[1], 5)


def merge_chains(lines: list[tuple[str, list, float]], joints=None) -> list[tuple[str, list, float]]:
    """Join ferry legs meeting at an end shared by exactly two legs.

    joints: if given, only ends in this set (no road: ferry_joint / floating)
    are merged; ports with a road keep their legs separate.

    Such an end is a stop on one sailing (False Pass -> Akutan -> Unalaska) or
    a mid-water joint. Akutan's dock has no road, so the grid's time there is
    a gap-fill guess (137 h, after Unalaska's 135.7) that hid the last hop;
    timing the merged chain from its two real ends avoids that.
    """
    lines = [(n, list(c), x) for n, c, x in lines]
    while True:
        ends: dict[tuple, list[int]] = {}
        for i, (_, c, _) in enumerate(lines):
            for pt in (c[0], c[-1]):
                ends.setdefault(_key(pt), []).append(i)
        pair = next(((k, v) for k, v in ends.items()
                     if len(v) == 2 and v[0] != v[1] and (joints is None or k in joints)), None)
        if pair is None:
            return lines
        k, (i, j) = pair
        (na, a, xa), (nb, b, xb) = lines[i], lines[j]
        if _key(a[0]) == k:
            a = a[::-1]                 # a now ends at k
        if _key(b[-1]) == k:
            b = b[::-1]                 # b now starts at k
        merged = (na if na == nb else f"{na} / {nb}", a + b[1:], xa + xb)
        lines = [ln for m, ln in enumerate(lines) if m not in (i, j)] + [merged]


def load(sample, path: Path = GEOJSON, skip=None) -> list[Ferry]:
    """sample(lon, lat) -> hours or None. skip(lon, lat) -> True drops a ferry."""
    feats = json.loads(path.read_text(encoding="utf-8"))["features"]
    # Ends with no road (the pipeline's connector report): grid times there
    # are gap-fill guesses, so legs meeting there are timed as one sailing.
    joints = {_key(f["geometry"]["coordinates"]) for f in feats
              if f["properties"].get("kind") == "endpoint"
              and f["properties"].get("status") in ("ferry_joint", "floating")}
    lines = []
    for f in feats:
        p = f["properties"]
        if p.get("kind") != "ferry" or f["geometry"]["type"] != "LineString":
            continue
        coords = f["geometry"]["coordinates"]
        if len(coords) < 2 or (skip and skip(*coords[0])):
            continue
        crossing = parse_duration(p.get("duration")) or length_km(coords) / UNTIMED_KMH
        lines.append((p.get("name") or "", coords, crossing))
    out = []
    for name, coords, crossing in merge_chains(lines, joints):
        got = classify(sample(*coords[0]), sample(*coords[-1]), crossing)
        if got is None:
            continue
        t0, t1, rev = got
        pts = np.array(coords[::-1] if rev else coords, dtype=float)
        out.append(Ferry(name, pts, t0, t1, crossing))
    return out


# Boat glyph, bow toward +x, ~pixels.
BOAT = [(13, 0), (5, 5), (-9, 5), (-11, 0), (-9, -5), (5, -5)]   # ~24 px long


def along(pts: np.ndarray, f: float):
    """Point and heading at fraction f of a screen-space polyline."""
    seg = np.hypot(*np.diff(pts, axis=0).T)
    cum = np.concatenate([[0], np.cumsum(seg)])
    d = f * cum[-1]
    i = int(np.clip(np.searchsorted(cum, d) - 1, 0, len(seg) - 1))
    u = 0.0 if seg[i] == 0 else (d - cum[i]) / seg[i]
    (x0, y0), (x1, y1) = pts[i], pts[i + 1]
    return x0 + u * (x1 - x0), y0 + u * (y1 - y0), math.atan2(y1 - y0, x1 - x0), i, cum[-1]


def draw(d, ferries: list[Ferry], t: float, to_px, size: tuple[int, int],
         water, trail, ink, linger_h: float = 1.5, min_px: float = 6.0):
    """Boats under way + wakes; used routes stay as faint lines afterwards."""
    w, h = size
    faint = tuple(int(water[k] + (trail[k] - water[k]) * 0.35) for k in range(3))
    for fr in ferries:
        if t < fr.t0:
            continue
        pts = np.column_stack(to_px(fr.lonlat[:, 0], fr.lonlat[:, 1]))
        xs, ys = pts[:, 0], pts[:, 1]
        if xs.max() < -20 or xs.min() > w + 20 or ys.max() < -20 or ys.min() > h + 20:
            continue
        if np.hypot(*np.diff(pts, axis=0).T).sum() < min_px:
            continue
        t1 = fr.t1_vis
        if t >= t1 + linger_h:
            d.line([tuple(p) for p in pts], fill=faint, width=1)
            continue
        f = min(1.0, (t - fr.t0) / (t1 - fr.t0))
        x, y, ang, i, _ = along(pts, f)
        fade = 1.0 if t <= t1 else 1 - (t - t1) / linger_h
        col = tuple(int(faint[k] + (trail[k] - faint[k]) * fade) for k in range(3))
        wake = [tuple(p) for p in pts[: i + 1]] + [(x, y)]
        if len(wake) >= 2:
            d.line(wake, fill=col, width=2)
        if t <= t1:
            c, s = math.cos(ang), math.sin(ang)
            hull = [(x + px * c - py * s, y + px * s + py * c) for px, py in BOAT]
            d.polygon(hull, fill=ink, outline=tuple(int(v) for v in water))
