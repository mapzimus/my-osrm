"""Lower-48 major roads as points every SPACING_M along each road.

The points are both where drive time is measured (drivetime.py) and the
road geometry that gets drawn (consecutive points of one road are joined).

    sh experiments/dunkin-vs-starbucks/roads.sh       # OSM -> cache/roads_us.geojsonseq
    python experiments/dunkin-vs-starbucks/roads.py   # -> cache/roads.npz

roads.npz: lon, lat (float32, one per point), start (int64: first point of
each road, plus a final end index), cls (uint8 per road: 0 motorway ..
4 tertiary).
"""

from __future__ import annotations

import json
import math
from array import array
from pathlib import Path

import numpy as np
import shapely
from shapely.ops import unary_union

from stores import STATE_SLOP_DEG, lower48

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
SPACING_M = 500
CLASSES = ["motorway", "trunk", "primary", "secondary", "tertiary"]


def resample(xy: np.ndarray, spacing: float) -> np.ndarray:
    """Points every <= spacing metres along a lon/lat polyline, ends included."""
    k = math.cos(math.radians(float(xy[:, 1].mean())))
    seg = np.hypot(np.diff(xy[:, 0]) * k, np.diff(xy[:, 1])) * 111_200
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    n = max(1, math.ceil(cum[-1] / spacing))
    at = np.linspace(0.0, cum[-1], n + 1)
    return np.column_stack([np.interp(at, cum, xy[:, 0]), np.interp(at, cum, xy[:, 1])])


def main() -> None:
    _, geoms, _ = lower48()
    land = unary_union(geoms).buffer(STATE_SLOP_DEG)
    shapely.prepare(land)
    lon, lat, start, cls = array("f"), array("f"), array("q"), array("B")
    n_ways = n_out = 0
    with open(CACHE / "roads_us.geojsonseq", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip("\x1e \n")
            if not line:
                continue
            f = json.loads(line)
            g, hw = f["geometry"], f["properties"].get("highway")
            if g["type"] != "LineString" or hw not in CLASSES:
                continue
            n_ways += 1
            xy = np.asarray(g["coordinates"], float)
            mid = xy[len(xy) // 2]
            if not (-125.5 < mid[0] < -66.5 and 24.0 < mid[1] < 49.6) or not shapely.contains_xy(land, *mid):
                n_out += 1
                continue
            pts = resample(xy, SPACING_M)
            start.append(len(lon))
            cls.append(CLASSES.index(hw))
            lon.extend(pts[:, 0].astype(np.float32))
            lat.extend(pts[:, 1].astype(np.float32))
            if n_ways % 500_000 == 0:
                print(f"  {n_ways:,} roads, {len(lon):,} points", flush=True)
    start.append(len(lon))
    np.savez(CACHE / "roads.npz", lon=np.frombuffer(lon, np.float32), lat=np.frombuffer(lat, np.float32),
             start=np.frombuffer(start, np.int64), cls=np.frombuffer(cls, np.uint8))
    print(f"{n_ways - n_out:,} roads in the lower 48 ({n_out:,} outside), {len(lon):,} points "
          f"every <= {SPACING_M} m")


if __name__ == "__main__":
    main()
