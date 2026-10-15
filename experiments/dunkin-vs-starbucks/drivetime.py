"""Drive time from the nearest store of each chain to every road point.

Every store "leaves" at once: for each road point (roads.py) the time is the
fastest drive FROM any store of that chain TO the point. To stay tractable,
each TILE_DEG tile of road points asks the engine only about the K nearest
stores (straight line) of each chain for each of its points; one /table per
tile, sources = those stores, destinations = the tile's points (split so no
request has more than MAX_COORDS coordinates; the engine allows 8000).
The engine's cost is per destination, so only one road point per CELL_DEG
cell (~1 km) is timed; every road point in the cell gets its times.

    python experiments/dunkin-vs-starbucks/drivetime.py --bench   # time 30 tiles, estimate the full run
    python experiments/dunkin-vs-starbucks/drivetime.py           # full run, resumable
    python experiments/dunkin-vs-starbucks/drivetime.py --stores experiments/hospitals/cache   # any stores.csv

Writes cache/drivetime.npz: dunkin_s, starbucks_s (float32, NaN = no route),
winner (uint8: index into chains, 255 none), arrival_s = the smallest, times
(chains x points), chains. Road points always come from this folder's cache/roads.npz;
--stores points at another stores.csv (one or more chains), outputs go next to it.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from stores import CHAINS

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
OSRM = "http://127.0.0.1:5002"     # docker-compose.yml: na-ferry engine, --max-table-size 8000
TILE_DEG = 0.25
CELL_DEG = 0.01                    # ~1 km: about a minute of driving, 2-3 frames of the video
K = 5
MAX_COORDS = 4000                  # under the engine's 8000, and keeps URLs ~40 KB
SAVE_EVERY = 300                   # tiles between checkpoints


def get(url: str, timeout: float = 600) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def polyline(lon: np.ndarray, lat: np.ndarray) -> str:
    """Google polyline (precision 5): short URLs for thousands of points."""
    out, plat, plon = [], 0, 0
    for x, y in zip(np.round(lon * 1e5).astype(int).tolist(), np.round(lat * 1e5).astype(int).tolist()):
        for v, p in ((y, plat), (x, plon)):
            d = v - p
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        plat, plon = y, x
    return urllib.request.quote("".join(out), safe="")


def xy(lon, lat) -> np.ndarray:
    """Points on a 6371 km sphere (x, y, z): chord distance ranks neighbours exactly.
    Was sinusoidal (lon * cos(lat)), which skews east-west distance far from Greenwich: for
    sparse sources it picked the wrong K nearest (Havre, MT got Omaha and Chicago instead of
    Salt Lake City, 14.6 h vs 9.7 h; airports, 2026-10-15)."""
    lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    return np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)]) * 6371.0


NAMES: list[str] = list(CHAINS)          # the chains being timed, set by load()


def load(folder: Path = CACHE):
    global NAMES
    r = np.load(CACHE / "roads.npz")
    lon, lat = r["lon"].astype(np.float64), r["lat"].astype(np.float64)
    stores: dict[str, list] = {}
    with open(folder / "stores.csv", encoding="utf-8") as fh:
        for s in csv.DictReader(fh):
            stores.setdefault(s["chain"], []).append((float(s["lon"]), float(s["lat"])))
    NAMES = list(CHAINS) if set(stores) == set(CHAINS) else list(stores)
    stores = {c: np.array(stores[c]) for c in NAMES}
    return lon, lat, stores


def tiles(lon: np.ndarray, lat: np.ndarray) -> list[np.ndarray]:
    key = np.floor(lon / TILE_DEG).astype(np.int64) * 10_000 + np.floor(lat / TILE_DEG).astype(np.int64)
    order = np.argsort(key, kind="stable")
    cuts = np.flatnonzero(np.diff(key[order])) + 1
    return np.split(order, cuts)


def solve_tile(idx, lon, lat, stores, trees) -> np.ndarray:
    """(len(NAMES), len(idx)) seconds from the nearest store of each chain."""
    p = xy(lon[idx], lat[idx])
    src_lon, src_lat, src_chain = [], [], []
    for ci, c in enumerate(NAMES):
        _, nn = trees[c].query(p, k=min(K, len(stores[c])))
        cand = np.unique(nn)
        src_lon += stores[c][cand, 0].tolist()
        src_lat += stores[c][cand, 1].tolist()
        src_chain += [ci] * len(cand)
    src_lon, src_lat, src_chain = np.array(src_lon), np.array(src_lat), np.array(src_chain)
    ns = len(src_lon)
    out = np.full((len(NAMES), len(idx)), np.nan, np.float32)
    step = MAX_COORDS - ns
    for a in range(0, len(idx), step):
        sub = idx[a:a + step]
        coords = polyline(np.concatenate([src_lon, lon[sub]]), np.concatenate([src_lat, lat[sub]]))
        d = get(f"{OSRM}/table/v1/driving/polyline({coords})?sources={';'.join(map(str, range(ns)))}"
                "&annotations=duration")["durations"]
        t = np.array([[np.nan if v is None else v for v in row[ns:]] for row in d], np.float32)
        for ci in range(len(NAMES)):
            rows = t[src_chain == ci]
            if len(rows):
                with np.errstate(all="ignore"):
                    out[ci, a:a + len(sub)] = np.where(np.isnan(rows).all(0), np.nan, np.nanmin(rows, 0))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", action="store_true", help="time 30 random tiles and estimate the full run")
    ap.add_argument("--workers", type=int, default=3, help="parallel requests to the engine")
    ap.add_argument("--stores", help="folder with stores.csv (chain, lon, lat); outputs go there")
    args = ap.parse_args()
    out_dir = Path(args.stores).resolve() if args.stores else CACHE

    lon_all, lat_all, stores = load(out_dir)
    key = np.round(lon_all / CELL_DEG).astype(np.int64) * 100_000 + np.round(lat_all / CELL_DEG).astype(np.int64)
    _, rep, cell_of = np.unique(key, return_index=True, return_inverse=True)
    lon, lat = lon_all[rep], lat_all[rep]          # one real road point per cell
    trees = {c: cKDTree(xy(v[:, 0], v[:, 1])) for c, v in stores.items()}
    ts = tiles(lon, lat)
    n = len(lon)
    print(f"{len(lon_all):,} road points, {n:,} timed (one per cell) in {len(ts):,} tiles; stores: "
          + ", ".join(f"{c} {len(v):,}" for c, v in stores.items()), flush=True)

    if args.bench:
        rng = np.random.default_rng(0)
        pick = rng.choice(len(ts), size=min(30, len(ts)), replace=False)
        t0 = time.time()
        with ThreadPoolExecutor(args.workers) as ex:
            res = list(ex.map(lambda i: solve_tile(ts[i], lon, lat, stores, trees), pick))
        dt = time.time() - t0
        pts = sum(len(ts[i]) for i in pick)
        miss = sum(int(np.isnan(r).any(0).sum()) for r in res)
        print(f"bench: {pts:,} points in {dt:.1f} s ({pts / dt:,.0f} points/s, {args.workers} workers); "
              f"{miss} points with a missing chain; full run ~{n / (pts / dt) / 60:.0f} min")
        return

    part = out_dir / "drivetime_partial.npz"
    times = np.full((len(NAMES), n), np.nan, np.float32)
    done = 0
    if part.exists():
        z = np.load(part)
        times, done = z["times"], int(z["done"])
        print(f"resuming at tile {done:,}", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(args.workers) as ex:
        for a in range(done, len(ts), SAVE_EVERY):
            batch = range(a, min(a + SAVE_EVERY, len(ts)))
            for i, r in zip(batch, ex.map(lambda i: solve_tile(ts[i], lon, lat, stores, trees), batch)):
                times[:, ts[i]] = r
            np.savez(part, times=times, done=batch[-1] + 1)
            el = time.time() - t0
            frac = (batch[-1] + 1 - done) / (len(ts) - done)
            print(f"  tiles {batch[-1] + 1:,}/{len(ts):,}, {el / 60:.1f} min, ~{el / frac / 60 - el / 60:.0f} min left",
                  flush=True)

    full = times[:, cell_of]
    filled = np.nan_to_num(full, nan=np.inf)
    winner = np.where(np.isinf(filled).all(0), 255, filled.argmin(0)).astype(np.uint8)
    arrival = np.where(winner == 255, np.nan, filled.min(0)).astype(np.float32)
    extra = {f"{c}_s": full[k] for k, c in enumerate(NAMES)}           # dunkin_s, starbucks_s, hospital_s...
    np.savez(out_dir / "drivetime.npz", winner=winner, arrival_s=arrival, times=full, chains=np.array(NAMES), **extra)
    part.unlink()
    won = np.bincount(winner[winner < 255], minlength=len(NAMES))
    print("done: " + ", ".join(f"{c} first on {w / won.sum():.1%}" for c, w in zip(NAMES, won))
          + f" of road points; {(winner == 255).sum():,} unreachable; slowest point {np.nanmax(arrival) / 3600:.1f} h")


if __name__ == "__main__":
    main()
