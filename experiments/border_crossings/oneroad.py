"""Idea #40, take two (Max, 2026-10-15): the ONE road that crosses the same state line the most.

The fastest-route search (search.py) tops out around 7: a fastest route jumps
onto a highway and skips the back roads that weave across a line. Here the
question is one road, driven end to end.

1. Every through road (motorway..unclassified, roads.sh) within NEAR_KM of a
   state line, grouped by route number ("US 64", each ref of "US 64;US 74"), or
   by name when it has none.
2. Score: distinct points where that road meets that border (merged within
   DEDUP_KM, so twin carriageways count once).
3. The top VERIFY candidates are driven by the engine through those points in
   order, and the route is re-counted with search.crossings() (each crossing
   reaches SIDE_KM into the other state; a road hugging the line can't score).
   The verified count is the one that goes on screen.

    sh experiments/border_crossings/roads.sh          # once: cache/roads_named.geojsonseq
    python experiments/border_crossings/oneroad.py

Writes cache/oneroad.json: verified candidates, best first, with waypoints.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import shapely

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
sys.path.insert(0, str(HERE))
from search import OSRM, albers, borders, crossings, decode, inverse, states  # noqa: E402

NEAR_KM = 8.0
DEDUP_KM = 0.3
VERIFY = 40
CELL = 0.02                                   # degrees, the near-a-border lookup grid
LON0, LAT0, NX, NY = -125.0, 24.0, int(60 / CELL), int(26 / CELL)


def near_grid(bd: dict) -> tuple[np.ndarray, list[str]]:
    """Grid cell -> index of a border within NEAR_KM (-1: none). Lines are in albers km."""
    names = sorted(bd)
    grid = np.full((NY, NX), -1, np.int16)
    for k, n in enumerate(names):
        zone = bd[n].buffer(NEAR_KM)
        x0, y0, x1, y1 = zone.bounds
        xs, ys = np.meshgrid(np.linspace(x0, x1, int((x1 - x0) / 1.5) + 2), np.linspace(y0, y1, int((y1 - y0) / 1.5) + 2))
        xs, ys = xs.ravel(), ys.ravel()
        inside = shapely.contains_xy(zone, xs, ys)
        lon, lat = inverse(xs[inside], ys[inside])
        cx = ((lon - LON0) / CELL).astype(int)
        cy = ((lat - LAT0) / CELL).astype(int)
        ok = (cx >= 0) & (cx < NX) & (cy >= 0) & (cy < NY)
        grid[cy[ok], cx[ok]] = k
    return grid, names


def keys(props: dict) -> list[str]:
    if props.get("ref"):
        return [r.strip() for r in props["ref"].split(";") if r.strip()]
    return [props["name"]] if props.get("name") else []


def collect(grid, names) -> dict:
    """(road key, border) -> list of lon/lat arrays of pieces near that border."""
    out = defaultdict(list)
    n = kept = 0
    t0 = time.time()
    with open(CACHE / "roads_named.geojsonseq", encoding="utf-8") as fh:
        for line in fh:
            n += 1
            if '"LineString"' not in line or ('"ref"' not in line and '"name"' not in line):
                continue
            f = json.loads(line.lstrip("\x1e"))
            c = np.asarray(f["geometry"]["coordinates"])
            cx = ((c[:, 0] - LON0) / CELL).astype(int)
            cy = ((c[:, 1] - LAT0) / CELL).astype(int)
            ok = (cx >= 0) & (cx < NX) & (cy >= 0) & (cy < NY)
            b = np.unique(grid[cy[ok], cx[ok]])
            b = b[b >= 0]
            if not len(b):
                continue
            kept += 1
            for k in keys(f["properties"]):
                for bi in b:
                    out[(k, names[bi])].append(c)
            if n % 2_000_000 == 0:
                print(f"  {n:,} features, {kept:,} near a border, {time.time() - t0:.0f} s", flush=True)
    return out


def score(pieces: list[np.ndarray], line) -> np.ndarray:
    """Distinct points (albers km) where these pieces meet the border."""
    geoms = [shapely.LineString(np.column_stack(albers(p[:, 0], p[:, 1]))) for p in pieces if len(p) > 1]
    hits = shapely.intersection(shapely.union_all(geoms), line) if geoms else None
    if hits is None or hits.is_empty:
        return np.zeros((0, 2))
    pts = np.array([g.coords[0] for g in getattr(hits, "geoms", [hits]) if g.geom_type == "Point"]
                   + [c for g in getattr(hits, "geoms", [hits]) if g.geom_type == "LineString" for c in g.coords[:1]])
    keep = []
    for p in pts:                                         # merge points closer than DEDUP_KM
        if all(np.hypot(*(p - q)) > DEDUP_KM for q in keep):
            keep.append(p)
    return np.array(keep)


def order(pts: np.ndarray) -> np.ndarray:
    """Visit order: start at one end of the farthest pair, then nearest-next."""
    d = np.hypot(*(pts[:, None, :] - pts[None, :, :]).transpose(2, 0, 1))
    i = int(np.unravel_index(d.argmax(), d.shape)[0])
    seq, left = [i], set(range(len(pts))) - {i}
    while left:
        j = min(left, key=lambda k: d[seq[-1], k])
        seq.append(j)
        left.remove(j)
    return pts[seq]


def main() -> None:
    t0 = time.time()
    st = states()
    bd = borders(st)
    grid, names = near_grid(bd)
    print(f"grid ready in {time.time() - t0:.0f} s", flush=True)
    groups = collect(grid, names)
    print(f"{len(groups):,} road x border groups in {time.time() - t0:.0f} s", flush=True)
    scored = []
    for (k, b), pieces in groups.items():
        pts = score(pieces, bd[b])
        if len(pts) >= 4:
            scored.append((len(pts), k, b, pts))
    scored.sort(key=lambda r: -r[0])
    print("raw top:", [(n, k, b) for n, k, b, _ in scored[:15]], flush=True)

    def fetch(cand):
        n, k, b, pts = cand
        seq = order(pts)
        lon, lat = inverse(seq[:, 0], seq[:, 1])
        coords = ";".join(f"{x:.6f},{y:.6f}" for x, y in zip(lon, lat))
        try:
            with urllib.request.urlopen(f"{OSRM}/route/v1/driving/{coords}?overview=full&geometries=polyline6",
                                        timeout=120) as r:
                rt = json.load(r)
        except OSError:
            return cand, seq, None
        return cand, seq, rt["routes"][0] if rt.get("code") == "Ok" else None

    out = []
    with ThreadPoolExecutor(6) as ex:
        for (n, k, b, pts), seq, rt in ex.map(fetch, scored[:VERIFY]):    # geometry stays in this thread
            if rt is None:
                continue
            a_, b_ = b.split("-")
            box = shapely.box(*shapely.MultiPoint(seq).buffer(60).bounds)
            pa, pb = shapely.intersection(st[a_], box), shapely.intersection(st[b_], box)
            ll = decode(rt["geometry"])
            c, _ = crossings(np.column_stack(albers(ll[:, 0], ll[:, 1])), pa, pb, bd[b])
            lon, lat = inverse(seq[:, 0], seq[:, 1])
            out.append(dict(road=k, border=b, raw=n, count=c, km=rt["distance"] / 1000, s=rt["duration"],
                            waypoints=[[float(x), float(y)] for x, y in zip(lon, lat)]))
    out.sort(key=lambda r: (-r["count"], r["s"]))
    json.dump(out, open(CACHE / "oneroad.json", "w"), indent=1)
    for r in out[:15]:
        print(f"  {r['count']:3d} (raw {r['raw']:3d})  {r['road']:<24} {r['border']}  {r['km']:.0f} km  {r['s'] / 60:.0f} min")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
