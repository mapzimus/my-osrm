"""Drive-time isochrone map: everywhere reachable from an origin, by hour.

Uses the local OSRM engine (US + Canada + ferries) and writes one
self-contained HTML map.

    # Regional: 1-8 h from Boston
    python experiments/isochrone/isochrone.py
    # Coast to coast: every hour it takes, Lower 48 + southern Canada
    python experiments/isochrone/isochrone.py --region national

How it works:
  1. Lay a grid over the region, spaced evenly in Web Mercator so the final
     image lines up pixel-for-pixel with the web basemap.
  2. Ask OSRM /table for origin -> every grid point. OSRM snaps each point to
     the nearest road and reports how far that snap was.
  3. Effective time = drive time + the snap distance at local-road speed.
     Points more than MAX_SNAP_M from any road (open water, roadless woods)
     are left blank rather than painted with a misleading time.
  4. Upsample + band into whole hours, paint as a transparent PNG, embed in
     Leaflet.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from matplotlib import colormaps
from matplotlib.image import imsave
import subprocess

from scipy.ndimage import binary_dilation, distance_transform_edt, zoom

import flights

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
OUT = HERE / "out"

BATCH = 400                # destinations per /table request (keeps URLs short)
MAX_SNAP_M = 5_000         # farther than this from a road = blank
LOCAL_SPEED_MS = 40 / 3.6  # off-network leg at local-road speed (40 km/h)
DAY_HOURS = 8              # one "driving day", for the long-trip legend
FILL_KM = 75               # land farther than this from any road stays blank
CAP_DAYS = 12              # long-trip ramp spans days 1-12 (Prudhoe Bay ~91 h)

# west, south, east, north; grid step (deg); max hours (0 = auto); upsample
REGIONS = {
    "regional": ((-82, 36.5, -59, 49.5), 0.04, 8, 4),
    "national": ((-127, 24.3, -52, 55), 0.05, 0, 2),
    # + Alaska/Yukon (only reachable through Canada), Mexico and Central
    # America to the Darien Gap (the road ends in Panama)
    "continental": ((-168, 7.0, -52, 71.5), 0.05, 0, 2),
}

# Labeled destinations; only those inside the map's bounds are drawn.
# The island ones only work because of the ferry build.
CITIES = {
    # Northeast
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
    # Rest of the continent
    "St. John's NL": (-52.7126, 47.5615), "Chicago": (-87.6298, 41.8781),
    "Detroit": (-83.0458, 42.3314), "Atlanta": (-84.3880, 33.7490),
    "Miami": (-80.1918, 25.7617), "Key West": (-81.7800, 24.5551),
    "Nashville": (-86.7816, 36.1627), "New Orleans": (-90.0715, 29.9511),
    "Minneapolis": (-93.2650, 44.9778), "Kansas City": (-94.5786, 39.0997),
    "Dallas": (-96.7970, 32.7767), "Houston": (-95.3698, 29.7604),
    "Winnipeg": (-97.1384, 49.8951), "Denver": (-104.9903, 39.7392),
    "Albuquerque": (-106.6504, 35.0844), "Calgary": (-114.0719, 51.0447),
    "Salt Lake City": (-111.8910, 40.7608), "Phoenix": (-112.0740, 33.4484),
    "Las Vegas": (-115.1398, 36.1699), "Boise": (-116.2023, 43.6150),
    "San Diego": (-117.1611, 32.7157), "Los Angeles": (-118.2437, 34.0522),
    "San Francisco": (-122.4194, 37.7749), "Portland OR": (-122.6765, 45.5231),
    "Seattle": (-122.3321, 47.6062), "Vancouver": (-123.1207, 49.2827),
    "Victoria BC": (-123.3656, 48.4284),
    # North
    "Whitehorse": (-135.0568, 60.7212), "Anchorage": (-149.9003, 61.2181),
    "Fairbanks": (-147.7164, 64.8378), "Prudhoe Bay": (-148.3360, 70.2550),
    "Homer AK": (-151.5483, 59.6425), "Haines AK": (-135.4450, 59.2358),
    "Yellowknife": (-114.3718, 62.4540),
    # Mexico
    "Tijuana": (-117.0382, 32.5149), "Monterrey": (-100.3161, 25.6866),
    "Mexico City": (-99.1332, 19.4326), "Guadalajara": (-103.3496, 20.6597),
    "Cancún": (-86.8515, 21.1619), "Oaxaca": (-96.7266, 17.0732),
    "Cabo San Lucas": (-109.9167, 22.8905), "Tapachula": (-92.2617, 14.9031),
    # Central America
    "Guatemala City": (-90.5069, 14.6349), "San Salvador": (-89.2182, 13.6929),
    "Managua": (-86.2514, 12.1150), "San José CR": (-84.0907, 9.9281),
    "Panama City": (-79.5199, 8.9824),
    # Islands reached by plane (flights.py), times sampled from the grid
    "Honolulu": (-157.8583, 21.3069), "Kahului": (-156.4729, 20.8893),
    "Hilo": (-155.0868, 19.7241), "Kailua-Kona": (-155.9969, 19.6400),
    "Lihue": (-159.3711, 21.9811), "Hana": (-155.9898, 20.7575),
    "San Juan": (-66.1057, 18.4655), "Ponce": (-66.6141, 18.0111),
    "Vieques": (-65.4434, 18.1494),
}


# Always labeled on long-trip maps; the rest get labels once zoomed in.
MAJOR = {
    "New York", "Washington DC", "Montreal", "Toronto", "Halifax", "St. John's NL",
    "Chicago", "Atlanta", "Miami", "Key West", "New Orleans", "Minneapolis", "Dallas",
    "Denver", "Winnipeg", "Calgary", "Salt Lake City", "Phoenix", "Los Angeles",
    "San Francisco", "Seattle", "Whitehorse", "Anchorage", "Fairbanks",
    "Prudhoe Bay", "Yellowknife", "Mexico City", "Monterrey", "Cancún",
    "Cabo San Lucas", "Guatemala City", "San José CR", "Panama City",
    "Honolulu", "San Juan",
}


def merc_y(lat: float) -> float:
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def inv_merc_y(y: np.ndarray) -> np.ndarray:
    return np.degrees(2 * np.arctan(np.exp(y)) - math.pi / 2)


def grid(bbox, step) -> tuple[np.ndarray, np.ndarray]:
    """Lon/lat grid, uniform in Web Mercator (rows north -> south)."""
    west, south, east, north = bbox
    lons = np.arange(west, east + 1e-9, step)
    y_step = math.radians(step)  # Mercator y and lon share units (radians)
    ys = np.arange(merc_y(north), merc_y(south) - 1e-12, -y_step)
    return lons, inv_merc_y(ys)


def table(url: str, origin, pts):
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


def drive_hours(url, origin, lons, lats, progress=None) -> np.ndarray:
    """Effective hours from origin to every grid cell (NaN = no road nearby).

    progress(done, total) is called per batch when given (the app's job
    progress bar); it may raise to cancel.
    """
    lon_g, lat_g = np.meshgrid(lons, lats)
    pts = list(zip(lon_g.ravel().tolist(), lat_g.ravel().tolist()))
    batches = [pts[i:i + BATCH] for i in range(0, len(pts), BATCH)]
    print(f"{len(pts):,} points in {len(batches)} requests", flush=True)
    durs, snaps = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, (d, s) in enumerate(pool.map(lambda b: table(url, origin, b), batches)):
            durs += d
            snaps += s
            if progress is not None:
                progress(i + 1, len(batches))
            elif i % 500 == 0:
                print(f"  {i}/{len(batches)}", flush=True)
    durs, snaps = np.array(durs), np.array(snaps)
    hours = (durs + snaps / LOCAL_SPEED_MS) / 3600
    hours[snaps > MAX_SNAP_M] = math.nan
    return hours.reshape(lat_g.shape).astype(np.float32)


def land_mask(grid_npz: Path, region: str, step: float) -> np.ndarray | None:
    """Land-minus-lakes mask for the grid (Natural Earth), built on first use."""
    mask_npz = CACHE / f"landmask_{region}_{step}.npz"
    ne = HERE.parent.parent / "data" / "naturalearth"
    if not mask_npz.exists():
        if not (ne / "ne_10m_land").exists():
            print("no Natural Earth data in data/naturalearth; skipping land fill")
            return None
        subprocess.run([
            "docker", "run", "--rm", "-v", f"{ne}:/ne:ro", "-v", f"{HERE}:/iso",
            "my-osrm-tools", "python", "/iso/landmask.py",
            f"/iso/cache/{grid_npz.name}", f"/iso/cache/{mask_npz.name}",
            "/ne/ne_10m_land/ne_10m_land.shp", "/ne/ne_10m_lakes/ne_10m_lakes.shp",
        ], check=True)
    return np.load(mask_npz)["mask"]


def fill_land(hours: np.ndarray, land: np.ndarray, lats: np.ndarray, step: float) -> np.ndarray:
    """Give every land cell the time of its nearest reachable cell + the gap.

    Mercator grid cells are physically square, side = step deg at cos(lat),
    so a plain cell-count distance transform is exact per row.
    """
    reachable = ~np.isnan(hours)
    dist_cells, (iy, ix) = distance_transform_edt(~reachable, return_indices=True)
    cell_km = 111.32 * step * np.cos(np.radians(lats))[:, None]
    gap_km = dist_cells * cell_km
    filled = hours[iy, ix] + gap_km / (LOCAL_SPEED_MS * 3.6)
    return np.where(land & (gap_km <= FILL_KM), filled, np.nan).astype(np.float32)


def palette_for(n: int) -> np.ndarray:
    """RGBA per band (index 0 = transparent).

    Short trips: viridis (inferno's bright end washes out on the light web
    basemap). Long trips: inferno run backwards, one solid color per
    DAY_HOURS-hour driving day: bright yellow near the origin fading through
    orange and red to deep purple far away. Perceptually even and
    colorblind-safe, unlike the turbo rainbow it replaced. The ramp stops at
    CAP_DAYS; later days (a few Aleutian specks) share the last color so the
    main map keeps the contrast. Hours inside a day alternate slightly
    lighter/darker so each hour stays a visible stripe.
    """
    pal = np.zeros((n + 1, 4), dtype=np.uint8)
    if n <= 12:
        cmap = colormaps["viridis"].resampled(n)
        rgb = [cmap(b)[:3] for b in range(n)]
    else:
        days = min(math.ceil(n / DAY_HOURS), CAP_DAYS)
        ramp = colormaps["inferno"]
        rgb = []
        for b in range(n):
            day, hour = divmod(b, DAY_HOURS)
            day = min(day, days - 1)
            # 0.95 -> 0.32: inferno below ~0.3 is near-black and vanishes on
            # the dark video background (Alaska went invisible at 0.15).
            base = np.array(ramp(0.95 - 0.63 * day / max(days - 1, 1))[:3])
            lift = 0.14 if hour % 2 else 0.0             # toward white
            rgb.append(base + (1 - base) * lift)
    for b, c in enumerate(rgb, start=1):
        pal[b] = (*(int(v * 255) for v in c), 200)
    return pal


def band_png(hours: np.ndarray, n: int, upsample: int) -> tuple[str, list[str]]:
    """Band into whole hours 1..n with crisp edges; return (base64 PNG, colors)."""
    filled = np.where(np.isnan(hours), 999.0, hours).astype(np.float32)
    smooth = zoom(filled, upsample, order=1)
    valid = zoom(np.isnan(hours).astype(np.float32), upsample, order=1) < 0.5
    band = np.ceil(smooth).astype(np.int32)        # 0-1 h -> band 1, etc.
    band[(band < 1) | (band > n) | ~valid] = 0
    palette = palette_for(n)
    rgba = palette[band]

    # Outline every region: thin darker line between hours, a solid dark
    # line between driving days (long trips only).
    edge = np.zeros(band.shape, dtype=bool)
    day_edge = np.zeros(band.shape, dtype=bool)
    day = np.where(band > 0, (band - 1) // DAY_HOURS, -1)
    for a, b, da, db in (
        (band[:, 1:], band[:, :-1], day[:, 1:], day[:, :-1]),
        (band[1:, :], band[:-1, :], day[1:, :], day[:-1, :]),
    ):
        diff = (a != b) & (a > 0) & (b > 0)
        ddiff = (da != db) & (a > 0) & (b > 0)
        if a.shape[0] == band.shape[0]:            # horizontal neighbors
            edge[:, 1:] |= diff
            day_edge[:, 1:] |= ddiff
        else:                                      # vertical neighbors
            edge[1:, :] |= diff
            day_edge[1:, :] |= ddiff
    rgba[edge, :3] = (rgba[edge, :3] * 0.72).astype(np.uint8)
    if n > 12:
        # Day lines 3 px wide so they survive zoomed-out viewing.
        thick = binary_dilation(day_edge, iterations=1) & (band > 0)
        rgba[thick] = (30, 30, 38, 240)
    buf = io.BytesIO()
    imsave(buf, rgba, format="png")
    hexes = ["#{:02x}{:02x}{:02x}".format(*palette[b][:3]) for b in range(1, n + 1)]
    return base64.b64encode(buf.getvalue()).decode(), hexes


def band_areas(hours: np.ndarray, lats: np.ndarray, step: float, n: int) -> list[float]:
    """Cumulative km^2 reachable within each hour (spherical cell areas)."""
    r = 6371.0
    dlon = math.radians(step)
    dlat = np.abs(np.gradient(np.radians(lats)))   # Mercator rows vary in lat
    cell = (r * r * dlon * dlat * np.cos(np.radians(lats)))[:, None]
    return [float(np.nansum(np.where(hours <= h, cell, 0))) for h in range(1, n + 1)]


def city_times(url, origin, bbox) -> list[dict]:
    west, south, east, north = bbox
    names = [n for n, (x, y) in CITIES.items() if west <= x <= east and south <= y <= north]
    durs, snaps = table(url, origin, [CITIES[n] for n in names])
    return [
        {"name": n, "lon": CITIES[n][0], "lat": CITIES[n][1], "major": n in MAJOR,
         "hours": None if math.isnan(d) else round((d + s / LOCAL_SPEED_MS) / 3600, 2)}
        for n, d, s in zip(names, durs, snaps)
    ]


def overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def air_hours(url, origin, lons, lats, bbox) -> tuple[np.ndarray, list]:
    """Hours to island road networks: fly from the home airport, then drive.

    Each arrival airport drives out over its island's cells; the fastest
    airport wins per cell (Big Island: Kona or Hilo). Cells outside every
    served island stay NaN.
    """
    home = flights.AIRPORTS["BOS"]
    d, s = table(url, origin, [home[1:]])
    legs = flights.plan((d[0] + s[0] / LOCAL_SPEED_MS) / 3600)
    lon_g, lat_g = np.meshgrid(lons, lats)
    out = np.full(lon_g.shape, np.nan, dtype=np.float32)
    used = []
    for leg in legs:
        isle = flights.SERVES[leg.dst]
        if not overlaps(isle, bbox):
            continue
        used.append(leg)
        cells = (lon_g >= isle[0]) & (lon_g <= isle[2]) & (lat_g >= isle[1]) & (lat_g <= isle[3])
        pts = list(zip(lon_g[cells].tolist(), lat_g[cells].tolist()))
        durs, snaps = [], []
        for i in range(0, len(pts), BATCH):
            dd, ss = table(url, flights.AIRPORTS[leg.dst][1:], pts[i:i + BATCH])
            durs += dd
            snaps += ss
        durs, snaps = np.array(durs), np.array(snaps)
        h = leg.ready + (durs + snaps / LOCAL_SPEED_MS) / 3600
        h[snaps > MAX_SNAP_M] = np.nan
        out[cells] = np.fmin(out[cells], h)
    return out, used


def region_spec(region) -> tuple[tuple, float, str]:
    """(bbox, step, label) for a REGIONS key or a custom (bbox, step) tuple."""
    if isinstance(region, str):
        bbox, step, _, _ = REGIONS[region]
        return bbox, step, region
    bbox, step = region
    w, s, e, n = bbox
    return tuple(bbox), step, f"custom_{w:.2f}_{s:.2f}_{e:.2f}_{n:.2f}"


def compute_hours(url: str, name: str, origin, region, progress=None, flights_on: bool = True):
    """Raw (unfilled) hours grid: driving, plus flights to islands. Cached.

    region: a REGIONS key, or (bbox, step) for a custom area.
    """
    bbox, step, label = region_spec(region)
    # the name becomes a cache filename: letters/digits/_/- only, no path parts
    safe = re.sub(r"[^a-z0-9_-]", "_", name.lower().replace(" ", "_")).strip("_") or "origin"
    slug = f"{safe[:60]}_{label}"
    lons, lats = grid(bbox, step)
    CACHE.mkdir(exist_ok=True)
    key = (CACHE / f"{slug}_{step}.npz").resolve()
    assert key.parent == CACHE.resolve()
    if key.exists():
        hours = np.load(key)["hours"]
        print(f"cached grid: {key.name}")
    else:
        t0 = time.time()
        hours = drive_hours(url, origin, lons, lats, progress=progress)
        np.savez_compressed(key, hours=hours, lons=lons, lats=lats)
        print(f"grid done in {time.time() - t0:.0f}s")
    if not flights_on:
        return hours, lons, lats, step, key, []
    air, legs = air_hours(url, origin, lons, lats, bbox)
    if legs:
        print("flights: " + ", ".join(f"{l.src}->{l.dst} ready {l.ready:.1f} h" for l in legs))
    return np.fmin(hours, air), lons, lats, step, key, legs


def sample(hours: np.ndarray, lons, lats, lon: float, lat: float) -> float | None:
    """Grid value at the cell nearest a point (for island cities)."""
    v = hours[int(np.abs(lats - lat).argmin()), int(np.abs(lons - lon).argmin())]
    return None if np.isnan(v) else round(float(v), 2)


def build_payload(url: str, name: str, origin, region, max_hours: int | None = None,
                  upsample: int | None = None, fill: bool = True, progress=None,
                  flights_on: bool = True) -> dict:
    """Everything the map needs: banded PNG, bounds, legend colors, areas, cities.

    region: REGIONS key or (bbox, step). max_hours/upsample default from REGIONS
    (custom regions: auto hours, upsample 2). fill=False skips the Natural
    Earth land fill (which shells out to Docker).
    """
    bbox, step, label = region_spec(region)
    if isinstance(region, str):
        _, _, r_hours, r_up = REGIONS[region]
    else:
        r_hours, r_up = 0, 2
    max_hours = r_hours if max_hours is None else max_hours
    upsample = r_up if upsample is None else upsample

    hours, lons, lats, step, key, legs = compute_hours(url, name, origin, region, progress=progress,
                                                       flights_on=flights_on)
    if fill:
        land = land_mask(key, label, step)
        if land is not None:
            hours = fill_land(hours, land, lats, step)

    if np.all(np.isnan(hours)):
        raise RuntimeError("no reachable cells: is the origin on the road network?")
    n = max_hours or int(math.ceil(np.nanmax(hours)))
    png, colors = band_png(hours, n, upsample)
    areas = band_areas(hours, lats, step, n)
    cities = city_times(url, origin, bbox)
    for c in cities:                 # islands: no road from the origin, use the grid
        if c["hours"] is None:
            c["hours"] = sample(hours, lons, lats, c["lon"], c["lat"])
    half = step / 2
    bounds = [[float(lats[-1]) - half, float(lons[0]) - half],
              [float(lats[0]) + half, float(lons[-1]) + half]]
    return {
        "name": name, "origin": list(origin), "png": png, "bounds": bounds,
        "colors": colors, "areas": areas, "cities": cities, "url": url,
        "hours": n, "dayHours": DAY_HOURS, "flights": flights.legs_json(legs),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5002")
    ap.add_argument("--origin", default="-71.0589,42.3601")
    ap.add_argument("--name", default="Boston")
    ap.add_argument("--region", choices=REGIONS, default="regional")
    args = ap.parse_args()
    origin = tuple(float(v) for v in args.origin.split(","))
    slug = f"{args.name.lower().replace(' ', '_')}_{args.region}"

    payload = build_payload(args.url, args.name, origin, args.region)
    n, cities = payload["hours"], payload["cities"]
    html = (HERE / "template.html").read_text(encoding="utf-8")
    OUT.mkdir(exist_ok=True)
    dest = OUT / f"isochrone_{slug}.html"
    dest.write_text(html.replace("/*__DATA__*/null", json.dumps(payload)), encoding="utf-8")
    print(f"wrote {dest} ({dest.stat().st_size / 1e6:.1f} MB), {n} hour bands")
    for c in sorted(cities, key=lambda c: c["hours"] or 999):
        print(f"  {c['hours'] if c['hours'] is not None else '  -':>6} h  {c['name']}")


if __name__ == "__main__":
    main()
