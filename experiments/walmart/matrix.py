"""Drive time and distance between every pair of points, fetched from the
engine in size x size blocks (each request carries at most 2 * size points,
so URLs stay short) and cached block by block: a crash resumes where it
stopped. NaN = the engine found no route."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def blocks(n: int, size: int) -> list[tuple[int, int]]:
    return [(a, min(a + size, n)) for a in range(0, n, size)]


def _block(pts, a, b, get, polyline, osrm):
    (a0, a1), (b0, b1) = a, b
    same = a == b
    sub = pts[a0:a1] if same else pts[a0:a1] + pts[b0:b1]
    la = a1 - a0
    src = ";".join(map(str, range(la)))
    dst = src if same else ";".join(map(str, range(la, la + b1 - b0)))
    d = get(f"{osrm}/table/v1/driving/polyline({polyline(sub)})?sources={src}&destinations={dst}"
            "&annotations=duration,distance")
    f = lambda rows: np.array([[np.nan if v is None else v for v in r] for r in rows], np.float32)  # noqa: E731
    return f(d["durations"]), f(d["distances"]), [w["location"] for w in d["sources"]]


def table(pts: list[list[float]], get, polyline, osrm: str, cache: Path, size: int = 500):
    cache.mkdir(parents=True, exist_ok=True)
    n = len(pts)
    key = cache / "points.json"
    if key.exists() and json.load(open(key)) != pts:          # different points: start over
        for f in cache.glob("*.npz"):
            f.unlink()
    json.dump(pts, open(key, "w"))
    dur = np.full((n, n), np.nan, np.float32)
    dist = np.full((n, n), np.nan, np.float32)
    road: list = [None] * n
    bl = blocks(n, size)
    for a in bl:
        for b in bl:
            f = cache / f"{a[0]}_{b[0]}.npz"
            if not f.exists():
                t, d, r = _block(pts, a, b, get, polyline, osrm)
                np.savez(f, t=t, d=d, r=np.array(r, np.float64))
            z = np.load(f)
            dur[a[0]:a[1], b[0]:b[1]] = z["t"]
            dist[a[0]:a[1], b[0]:b[1]] = z["d"]
            if road[a[0]] is None:
                road[a[0]:a[1]] = z["r"].tolist()
        print(f"  matrix rows {a[1]:,}/{n:,}", flush=True)
    return dur, dist, road
