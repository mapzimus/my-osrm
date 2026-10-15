"""Idea #40: the drive that crosses the same state border the most times.

Only real fastest routes count, no forced waypoints: for every pair of
neighbouring states, points every STEP_KM along their shared border are paired
with the points SEP_KM further along (MIN..MAX), the engine routes between
them, and the route's crossings of that border are counted. A crossing is a
change of state that goes at least SIDE_KM into the other state, so a road
hugging the line (State Line Road) or a wobble in the border data can't score.

    python experiments/border_crossings/search.py --border TN-VA   # one border, quick test
    python experiments/border_crossings/search.py                  # every border, resumable

Borders: Census TIGER/Line 2024 states (full resolution), cache/states/.
Writes cache/results.json: per border, the best routes (count, endpoints, km, s).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import shapefile
import shapely
from shapely.geometry import shape

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
sys.path.insert(0, str(HERE.parent / "dunkin-vs-starbucks"))
from draw import albers  # noqa: E402  (equal-area km, lower 48)

OSRM = "http://127.0.0.1:5002"
STEP_KM = 4.0                    # border sample spacing
MIN_KM, MAX_KM = 12.0, 160.0     # along-border separation of the two ends
SIDE_KM = 0.1                    # a crossing must reach this far into the other state
DENSIFY_KM = 0.03                # route points every 30 m before labelling
WORKERS = 8
NOT_L48 = {"AK", "HI", "PR", "GU", "VI", "AS", "MP"}


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r)


def states() -> dict[str, shapely.Geometry]:
    """Lower-48 states + DC in albers km."""
    sf = shapefile.Reader(str(CACHE / "states" / "tl_2024_us_state.shp"))
    out = {}
    for s, r in zip(sf.shapes(), sf.records()):
        if r["STUSPS"] in NOT_L48:
            continue
        g = shape(s.__geo_interface__)
        out[r["STUSPS"]] = shapely.transform(g, lambda c: np.column_stack(albers(c[:, 0], c[:, 1])))
    return out


def borders(st: dict) -> dict[str, shapely.Geometry]:
    """'AA-BB' -> shared border line (albers km), for every touching pair."""
    keys = sorted(st)
    tree = shapely.STRtree([st[k] for k in keys])
    out = {}
    for i, a in enumerate(keys):
        for j in tree.query(st[a], predicate="intersects"):
            b = keys[j]
            if b <= a:
                continue
            line = shapely.line_merge(shapely.intersection(st[a].boundary, st[b].boundary))
            if line.length > 5:
                out[f"{a}-{b}"] = line
    return out


def inverse(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """albers km -> lon/lat by Newton steps on a numeric Jacobian (accurate to ~1 m)."""
    lon, lat = np.full_like(x, -96.0), np.full_like(y, 38.0)
    for _ in range(8):
        fx, fy = albers(lon, lat)
        ex, ey = albers(lon + 1e-4, lat)
        gx, gy = albers(lon, lat + 1e-4)
        a, b, c, d = (ex - fx) / 1e-4, (gx - fx) / 1e-4, (ey - fy) / 1e-4, (gy - fy) / 1e-4
        det = a * d - b * c
        rx, ry = x - fx, y - fy
        lon, lat = lon + (d * rx - b * ry) / det, lat + (a * ry - c * rx) / det
    return lon, lat


def decode(poly: str, prec: float = 1e6) -> np.ndarray:
    pts, i, lat, lon = [], 0, 0, 0
    while i < len(poly):
        for k in range(2):
            shift = res = 0
            while True:
                b = ord(poly[i]) - 63
                i += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(res >> 1) if res & 1 else res >> 1
            if k == 0:
                lat += d
            else:
                lon += d
        pts.append((lon / prec, lat / prec))
    return np.array(pts)


def crossings(xy: np.ndarray, a_poly, b_poly, line) -> tuple[int, list[int]]:
    """Count A<->B changes on a route (albers km). Returns (count, index of each crossing)."""
    seg = np.hypot(*np.diff(xy, axis=0).T)
    n = np.maximum(1, np.ceil(seg / DENSIFY_KM).astype(int))
    t = np.concatenate([np.arange(k) / k for k in n] + [[0.0]])
    base = np.concatenate([np.full(k, i) for i, k in enumerate(n)] + [[len(xy) - 1]])
    nxt = np.minimum(base + 1, len(xy) - 1)
    pts = xy[base] + (xy[nxt] - xy[base]) * t[:, None]
    near = shapely.distance(line, shapely.points(pts)) < SIDE_KM
    in_a = shapely.contains_xy(a_poly, pts[:, 0], pts[:, 1])
    in_b = shapely.contains_xy(b_poly, pts[:, 0], pts[:, 1])
    lab = np.where(near, 0, np.where(in_a, 1, np.where(in_b, 2, 3)))
    count, at, last = 0, [], 0
    for i, v in enumerate(lab):
        if v in (1, 2):
            if last and v != last:
                count += 1
                at.append(int(base[i]))
            last = v
        elif v == 3:
            last = 0                       # a third state breaks the run: that's not this border
    return count, at


def search(name: str, line, st: dict, ex: ThreadPoolExecutor) -> list[dict]:
    a, b = name.split("-")
    box = shapely.box(*line.buffer(60).bounds)
    a_poly, b_poly = shapely.intersection(st[a], box), shapely.intersection(st[b], box)
    for g in (a_poly, b_poly, line):
        shapely.prepare(g)
    out = []
    parts = list(line.geoms) if hasattr(line, "geoms") else [line]
    jobs = []
    for part in parts:
        d = np.arange(0, part.length, STEP_KM)
        pts = np.array([part.interpolate(v).coords[0] for v in d])
        lon, lat = inverse(pts[:, 0], pts[:, 1])
        for i in range(len(d)):
            for j in range(i + 1, len(d)):
                if MIN_KM <= d[j] - d[i] <= MAX_KM:
                    jobs.append((lon[i], lat[i], lon[j], lat[j]))

    def fetch(job):
        """Threads only fetch: GEOS prepared geometries are not thread-safe (access violation, 2026-10-15)."""
        lo0, la0, lo1, la1 = job
        try:
            r = get(f"{OSRM}/route/v1/driving/{lo0:.6f},{la0:.6f};{lo1:.6f},{la1:.6f}"
                    "?overview=full&geometries=polyline6")
        except OSError:
            return job, None
        return job, (r["routes"][0] if r.get("code") == "Ok" else None)

    for (lo0, la0, lo1, la1), rt in ex.map(fetch, jobs):
        if rt is None:
            continue
        ll = decode(rt["geometry"])
        c, _ = crossings(np.column_stack(albers(ll[:, 0], ll[:, 1])), a_poly, b_poly, line)
        if c >= 3:
            out.append(dict(border=name, count=c, start=[lo0, la0], end=[lo1, la1], km=rt["distance"] / 1000,
                            s=rt["duration"]))
    out.sort(key=lambda r: (-r["count"], r["s"]))
    return out[:20], len(jobs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--border", help="one border, e.g. TN-VA; or several, comma-separated")
    ap.add_argument("--max-km", type=float, help="longest along-border separation (default MAX_KM)")
    ap.add_argument("--out", default="results.json", help="results file in cache/")
    args = ap.parse_args()
    global MAX_KM
    MAX_KM = args.max_km or MAX_KM
    t0 = time.time()
    st = states()
    bd = borders(st)
    print(f"{len(bd)} borders, {sum(l.length for l in bd.values()):,.0f} km in {time.time() - t0:.0f} s", flush=True)
    path = CACHE / args.out
    done = json.load(open(path)) if path.exists() else {}
    names = args.border.split(",") if args.border else sorted(bd, key=lambda k: bd[k].length)
    with ThreadPoolExecutor(WORKERS) as ex:
        for name in names:
            if name in done:
                continue
            t1 = time.time()
            best, n = search(name, bd[name], st, ex)
            done[name] = best
            top = best[0]["count"] if best else 0
            print(f"{name}: {n:,} routes, best {top} crossings ({time.time() - t1:.0f} s)", flush=True)
            json.dump(done, open(path, "w"))
    ranked = sorted((r for v in done.values() for r in v[:1]), key=lambda r: (-r["count"], r["s"]))
    for r in ranked[:15]:
        print(f"  {r['count']:3d}  {r['border']}  {r['km']:.0f} km  {r['s'] / 60:.0f} min  "
              f"{r['start'][1]:.4f},{r['start'][0]:.4f} -> {r['end'][1]:.4f},{r['end'][0]:.4f}")


if __name__ == "__main__":
    main()
