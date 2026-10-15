"""Idea #224: every road to its nearest shore, drawn like river basins.

Four shores (Max, 2026-10-15): Atlantic, Pacific, Gulf of Mexico, Great Lakes.
Shoreline: Natural Earth 10m land outline (oceans and the Gulf) and the five Great
Lakes (ne_10m_lakes), near the lower 48 (Census TIGER outline + 0.15 deg). Sampled
every STEP_DEG; each sample snapped to the nearest road (OSRM /nearest) and kept
when the road is within SNAP_M: a shore you can drive to. The Gulf is the coast from
Brownsville to Florida's west coast (lat < 31, lon < -81.25, Keys excluded); west of
-114 is Pacific; the rest of the ocean coast is Atlantic.

Data probe (2026-10-15): roads reach 76% of Pacific, 66% Atlantic, 62% Great Lakes,
41% Gulf samples (Louisiana marsh); Denver is a near three-way tie (Pacific 15.5 h,
Great Lakes 15.9 h, Gulf 16.2 h).

    python experiments/coasts/inventory.py      # engine on :5002

Writes cache/stores.csv (chain = shore, lon, lat of the road point) for
experiments/dunkin-vs-starbucks/drivetime.py --stores experiments/coasts/cache.
"""

from __future__ import annotations

import csv
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import shapefile
import shapely
from shapely.geometry import shape

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
ROOT = HERE.parents[1]
NE = ROOT / "data" / "naturalearth"
TIGER = HERE.parent / "border_crossings" / "cache" / "states" / "tl_2024_us_state.shp"
OSRM = "http://127.0.0.1:5002"
STEP_DEG = 0.05                      # ~5 km
SNAP_M = 2000
LAKES = ("Lake Superior", "Lake Michigan", "Lake Huron", "Lake Erie", "Lake Ontario")
SHORES = ["Atlantic", "Gulf", "Pacific", "Great Lakes"]


def sample(g) -> list[tuple[float, float]]:
    out = []
    for part in getattr(g, "geoms", [g]):
        if part.geom_type != "LineString":
            continue
        n = max(1, int(part.length / STEP_DEG))
        out += [part.interpolate(i / n, normalized=True).coords[0] for i in range(n + 1)]
    return out


def ocean(lon: float, lat: float) -> str:
    """Pacific west of -114. Gulf by which side of Florida a point is on (Max, 2026-10-15: the first rule,
    lat < 31 and lon < -81.25, put Jacksonville-St. Augustine and south Georgia on the Gulf):
    north of 28 N only west of -82.2 (Big Bend, Panhandle and west); 25.3-28 N west of -81.15
    (Tampa to Naples); the Keys and everything east are Atlantic."""
    if lon < -114:
        return "Pacific"
    if lat < 31.0 and ((lat >= 28.0 and lon < -82.2) or (25.3 <= lat < 28.0 and lon < -81.15)):
        return "Gulf"
    return "Atlantic"


def reclassify() -> None:
    """Relabel cache/stores.csv with ocean() (no re-snapping: the road points are within 2 km)."""
    rows = list(csv.DictReader(open(CACHE / "stores.csv", encoding="utf-8")))
    moved = 0
    for r in rows:
        if r["chain"] != "Great Lakes":
            new = ocean(float(r["lon"]), float(r["lat"]))
            moved += new != r["chain"]
            r["chain"] = r["name"] = new
    with open(CACHE / "stores.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["chain", "name", "lon", "lat"])
        w.writeheader()
        w.writerows(rows)
    print(f"relabelled {moved} shore points")


def snap(p):
    lon, lat = p
    try:
        with urllib.request.urlopen(f"{OSRM}/nearest/v1/driving/{lon:.5f},{lat:.5f}", timeout=30) as r:
            w = json.load(r)["waypoints"][0]
        return w["location"] if w["distance"] < SNAP_M else None
    except OSError:
        return None


def main() -> None:
    sf = shapefile.Reader(str(TIGER))
    us = shapely.union_all([shape(s.__geo_interface__) for s, r in zip(sf.shapes(), sf.records())
                            if r["STUSPS"] not in ("AK", "HI", "PR", "GU", "VI", "AS", "MP")])
    near = us.buffer(0.15)
    land = shapely.union_all([shape(s.__geo_interface__)
                              for s in shapefile.Reader(str(NE / "ne_10m_land" / "ne_10m_land.shp")).shapes()])
    lk = shapefile.Reader(str(NE / "ne_10m_lakes" / "ne_10m_lakes.shp"))
    great = shapely.union_all([shape(s.__geo_interface__) for s, r in zip(lk.shapes(), lk.records())
                               if r["name"] in LAKES])
    pts = [(ocean(*p), p) for p in sample(shapely.intersection(land.boundary, near))]
    pts += [("Great Lakes", p) for p in sample(shapely.intersection(great.boundary, near))]
    with ThreadPoolExecutor(8) as ex:
        snapped = list(ex.map(snap, [p for _, p in pts]))
    seen, rows = set(), []
    for (shore, _), s in zip(pts, snapped):
        if s is None:
            continue
        key = (shore, round(s[0], 4), round(s[1], 4))          # two samples on one road point: once
        if key not in seen:
            seen.add(key)
            rows.append(dict(chain=shore, name=shore, lon=s[0], lat=s[1]))
    with open(CACHE / "stores.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["chain", "name", "lon", "lat"])
        w.writeheader()
        w.writerows(rows)
    n = {k: sum(1 for r in rows if r["chain"] == k) for k in SHORES}
    print(f"{len(rows):,} drivable shore points from {len(pts):,} samples: {n}")


if __name__ == "__main__":
    import sys
    reclassify() if "--reclassify" in sys.argv else main()
