"""Drive-time isochrone map: everywhere reachable from an origin in 1..8 hours.

Uses the local OSRM engine (US + Canada + ferries) and writes one
self-contained HTML map.

    python experiments/isochrone/isochrone.py                 # Boston, :5002
    python experiments/isochrone/isochrone.py --origin -71.0589,42.3601 --name Boston

How it works:
  1. Lay a grid over the region, spaced evenly in Web Mercator so the final
     image lines up pixel-for-pixel with the web basemap.
  2. Ask OSRM /table for origin -> every grid point. OSRM snaps each point to
     the nearest road and reports how far that snap was.
  3. Effective time = drive time + the snap distance at local-road speed.
     Points more than MAX_SNAP_M from any road (open water, roadless woods)
     are left blank rather than painted with a misleading time.
  4. Upsample + band into hours, paint as a transparent PNG, embed in Leaflet.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from matplotlib import colormaps
from matplotlib.image import imsave
from scipy.ndimage import zoom

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
OUT = HERE / "out"

HOURS = 8
STEP_DEG = 0.04            # grid spacing in longitude (~3.3 km at 42N)
BATCH = 400                # destinations per /table request (keeps URLs short)
MAX_SNAP_M = 5_000         # farther than this from a road = blank
LOCAL_SPEED_MS = 40 / 3.6  # off-network leg at local-road speed (40 km/h)
UPSAMPLE = 4

# Labeled destinations. Island ones only work because of the ferry build.
CITIES = {
    "New York": (-73.9857, 40.7484), "Philadelphia": (-75.1652, 39.9526),
    "Washington DC": (-77.0365, 38.8977), "Pittsburgh": (-79.9959, 40.4406),
    "Albany": (-73.7562, 42.6526), "Buffalo": (-78.8784, 42.8864),
    "Burlington VT": (-73.2121, 44.4759), "Portland ME": (-70.2553, 43.6591),
    "Bar Harbor": (-68.2039, 44.3876), "Montreal": (-73.5673, 45.5017),
    "Quebec City": (-71.2080, 46.8139), "Ottawa": (-75.6972, 45.4215),
    "Toronto": (-79.3832, 43.6532), "Halifax": (-63.5752, 44.6488),
    "Saint John NB": (-66.0633, 45.2733), "Provincetown": (-70.1860, 42.0584),
    "Nantucket": (-70.0995, 41.2835), "Martha's Vineyard": (-70.5990, 41.4543),
    "Block Island": (-71.5772, 41.1720), "Montauk": (-71.9545, 41.0359),
    "Charlottetown PEI": (-63.1311, 46.2382),
}


def merc_y(lat: float) -> float:
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def inv_merc_y(y: np.ndarray) -> np.ndarray:
    return np.degrees(2 * np.arctan(np.exp(y)) - math.pi / 2)


def grid(bbox: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Lon/lat grid, uniform in Web Mercator (rows north -> south)."""
    west, south, east, north = bbox
    lons = np.arange(west, east + 1e-9, STEP_DEG)
    y_step = math.radians(STEP_DEG)  # Mercator y and lon share units (radians)
    ys = np.arange(merc_y(north), merc_y(south) - 1e-12, -y_step)
    return lons, inv_merc_y(ys)


def table(url: str, origin: tuple[float, float], pts: list[tuple[float, float]]):
    coords = ";".join(f"{x:.5f},{y:.5f}" for x, y in [origin, *pts])
    req = f"{url}/table/v1/driving/{coords}?sources=0&annotations=duration"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.load(resp)
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2)
    durs = [d if d is not None else math.nan for d in data["durations"][0][1:]]
    snaps = [d["distance"] for d in data["destinations"][1:]]
    return durs, snaps


def drive_hours(url, origin, lons, lats) -> np.ndarray:
    """Effective hours from origin to every grid cell (NaN = no road nearby)."""
    lon_g, lat_g = np.meshgrid(lons, lats)
    pts = list(zip(lon_g.ravel().tolist(), lat_g.ravel().tolist()))
    batches = [pts[i:i + BATCH] for i in range(0, len(pts), BATCH)]
    print(f"{len(pts):,} points in {len(batches)} requests", flush=True)
    durs, snaps = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, (d, s) in enumerate(pool.map(lambda b: table(url, origin, b), batches)):
            durs += d
            snaps += s
            if i % 50 == 0:
                print(f"  {i}/{len(batches)}", flush=True)
    durs, snaps = np.array(durs), np.array(snaps)
    hours = (durs + snaps / LOCAL_SPEED_MS) / 3600
    hours[snaps > MAX_SNAP_M] = math.nan
    return hours.reshape(lat_g.shape)


def band_png(hours: np.ndarray) -> tuple[str, list[str]]:
    """Smooth, band into whole hours, return (base64 PNG, band colors)."""
    filled = np.where(np.isnan(hours), 99.0, hours)
    smooth = zoom(filled, UPSAMPLE, order=1)
    valid = zoom(np.isnan(hours).astype(float), UPSAMPLE, order=1) < 0.5
    band = np.ceil(smooth).astype(int)          # 0-1 h -> band 1, etc.
    cmap = colormaps["viridis"].resampled(HOURS)
    colors = [cmap(i) for i in range(HOURS)]
    rgba = np.zeros((*band.shape, 4))
    for b in range(1, HOURS + 1):
        rgba[(band == b) & valid] = colors[b - 1]
    rgba[..., 3] = np.where(rgba[..., 3] > 0, 0.62, 0)
    buf = io.BytesIO()
    imsave(buf, rgba, format="png")
    hexes = ["#{:02x}{:02x}{:02x}".format(*(int(c * 255) for c in col[:3])) for col in colors]
    return base64.b64encode(buf.getvalue()).decode(), hexes


def band_areas(hours: np.ndarray, lats: np.ndarray) -> list[float]:
    """Cumulative km^2 reachable within each hour (spherical cell areas)."""
    r = 6371.0
    dlon = math.radians(STEP_DEG)
    # Row height in latitude varies (Mercator grid); use neighbor spacing.
    dlat = np.abs(np.gradient(np.radians(lats)))
    cell = (r * r * dlon * dlat * np.cos(np.radians(lats)))[:, None] * np.ones_like(hours)
    return [float(np.nansum(np.where(hours <= h, cell, 0))) for h in range(1, HOURS + 1)]


def city_times(url, origin) -> list[dict]:
    names = list(CITIES)
    durs, snaps = table(url, origin, [CITIES[n] for n in names])
    return [
        {"name": n, "lon": CITIES[n][0], "lat": CITIES[n][1],
         "hours": None if math.isnan(d) else round((d + s / LOCAL_SPEED_MS) / 3600, 2)}
        for n, d, s in zip(names, durs, snaps)
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5002")
    ap.add_argument("--origin", default="-71.0589,42.3601")
    ap.add_argument("--name", default="Boston")
    ap.add_argument("--bbox", default="-82,36.5,-59,49.5", help="west,south,east,north")
    args = ap.parse_args()
    origin = tuple(float(v) for v in args.origin.split(","))
    bbox = tuple(float(v) for v in args.bbox.split(","))

    lons, lats = grid(bbox)
    CACHE.mkdir(exist_ok=True)
    key = CACHE / f"{args.name.lower().replace(' ', '_')}_{STEP_DEG}.npz"
    if key.exists():
        hours = np.load(key)["hours"]
        print(f"cached grid: {key.name}")
    else:
        t0 = time.time()
        hours = drive_hours(args.url, origin, lons, lats)
        np.savez_compressed(key, hours=hours, lons=lons, lats=lats)
        print(f"grid done in {time.time() - t0:.0f}s")

    png, colors = band_png(hours)
    areas = band_areas(hours, lats)
    cities = city_times(args.url, origin)
    # Image bounds: cell centers +/- half a cell, in lat/lon for Leaflet.
    half = STEP_DEG / 2
    bounds = [[float(lats[-1]) - half, float(lons[0]) - half],
              [float(lats[0]) + half, float(lons[-1]) + half]]

    html = (HERE / "template.html").read_text(encoding="utf-8")
    payload = {
        "name": args.name, "origin": origin, "png": png, "bounds": bounds,
        "colors": colors, "areas": areas, "cities": cities, "url": args.url,
        "hours": HOURS,
    }
    OUT.mkdir(exist_ok=True)
    dest = OUT / f"isochrone_{args.name.lower().replace(' ', '_')}.html"
    dest.write_text(html.replace("/*__DATA__*/null", json.dumps(payload)), encoding="utf-8")
    print(f"wrote {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    for c in sorted(cities, key=lambda c: c["hours"] or 99):
        print(f"  {c['hours'] if c['hours'] is not None else '  -':>5} h  {c['name']}")


if __name__ == "__main__":
    main()
