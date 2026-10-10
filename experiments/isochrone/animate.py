"""Animated drive-time wave: color spreads out from the origin hour by hour.

Reads the cached continental grid from isochrone.py and renders an
Instagram-ready MP4 (+ a small GIF) in an equal-area projection.

    python experiments/isochrone/animate.py                  # 1080x1350 feed
    python experiments/isochrone/animate.py --format reel    # 1080x1920

Frames are piped straight into ffmpeg; nothing is written per frame.
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import binary_dilation, distance_transform_edt, map_coordinates

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import isochrone as iso  # noqa: E402  (reuse grid, palette, land mask, cities)

FONTS = Path("C:/Windows/Fonts")
FPS = 30
WAVE_SECONDS = 14
HOLD_SECONDS = 2.5
SS = 2                                   # supersampling for smooth edges

WATER = np.array([13, 17, 23], dtype=np.float32)
LAND = np.array([33, 38, 48], dtype=np.float32)
INK = (236, 239, 244)
MUTED = (150, 158, 172)

# Cities that get a label in the video (the full list is too dense at 1080 px).
VIDEO_CITIES = [
    "New York", "Washington DC", "Montreal", "St. John's NL", "Chicago",
    "Atlanta", "Miami", "Dallas", "Winnipeg", "Denver", "Calgary",
    "Seattle", "San Francisco", "Los Angeles", "Whitehorse", "Anchorage", "Prudhoe Bay",
    "Monterrey", "Mexico City", "Cancún", "Cabo San Lucas",
]
LEFT_LABELS = {"Seattle", "San Francisco", "Los Angeles", "Anchorage", "Key West",
               "Chicago", "St. John's NL"}

FORMATS = {
    # canvas, map box (x0, y0, x1, y1), title y. Reels keep everything inside
    # Instagram's safe zone (UI covers ~220 px top, ~420 px bottom).
    "feed": ((1080, 1350), (30, 190, 1050, 1090), 48),
    "reel": ((1080, 1920), (30, 400, 1050, 1300), 250),
}


class Albers:
    """Spherical Albers equal-area conic tuned for North America (ESRI:102008)."""

    def __init__(self, lon0=-96.0, lat0=40.0, lat1=20.0, lat2=60.0):
        p1, p2, p0 = map(math.radians, (lat1, lat2, lat0))
        self.lon0 = math.radians(lon0)
        self.n = (math.sin(p1) + math.sin(p2)) / 2
        self.c = math.cos(p1) ** 2 + 2 * self.n * math.sin(p1)
        self.rho0 = math.sqrt(self.c - 2 * self.n * math.sin(p0)) / self.n

    def forward(self, lon, lat):
        theta = self.n * (np.radians(lon) - self.lon0)
        rho = np.sqrt(self.c - 2 * self.n * np.sin(np.radians(lat))) / self.n
        return rho * np.sin(theta), self.rho0 - rho * np.cos(theta)

    def inverse(self, x, y):
        rho = np.hypot(x, self.rho0 - y)
        theta = np.arctan2(x, self.rho0 - y)
        s = np.clip((self.c - (rho * self.n) ** 2) / (2 * self.n), -1, 1)
        return np.degrees(self.lon0 + theta / self.n), np.degrees(np.arcsin(s))


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def load_field(name: str):
    """Hours everywhere (nearest-road filled) + where it's valid land."""
    bbox, step, _, _ = iso.REGIONS["continental"]
    slug = f"{name.lower().replace(' ', '_')}_continental"
    grid = np.load(iso.CACHE / f"{slug}_{step}.npz")
    hours, lons, lats = grid["hours"], grid["lons"], grid["lats"]
    land = iso.land_mask(iso.CACHE / f"{slug}_{step}.npz", "continental", step)
    reachable = ~np.isnan(hours)
    dist, (iy, ix) = distance_transform_edt(~reachable, return_indices=True)
    gap_km = dist * 111.32 * step * np.cos(np.radians(lats))[:, None]
    filled = hours[iy, ix] + gap_km / (iso.LOCAL_SPEED_MS * 3.6)
    valid = land & (gap_km <= iso.FILL_KM)
    return filled.astype(np.float32), valid, land, lons, lats, step


def rasterize(proj, filled, valid, land, lons, lats, step, box):
    """Sample the lon/lat grid onto the projected map box (at SS x)."""
    x0, y0, x1, y1 = box
    w, h = (x1 - x0) * SS, (y1 - y0) * SS
    # Fit the extent of valid land into the box, preserving aspect.
    lg, tg = np.meshgrid(lons, lats)
    px, py = proj.forward(lg[valid], tg[valid])
    ex0, ex1, ey0, ey1 = px.min(), px.max(), py.min(), py.max()
    scale = min(w / (ex1 - ex0), h / (ey1 - ey0))
    cx, cy = (ex0 + ex1) / 2, (ey0 + ey1) / 2
    xs = cx + (np.arange(w) + 0.5 - w / 2) / scale
    ys = cy - (np.arange(h) + 0.5 - h / 2) / scale
    X, Y = np.meshgrid(xs, ys)
    lon, lat = proj.inverse(X, Y)
    col = (lon - lons[0]) / step
    row = (iso.merc_y(float(lats[0])) - np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))) / math.radians(step)
    inside = (col >= 0) & (col <= len(lons) - 1) & (row >= 0) & (row <= len(lats) - 1)
    H = map_coordinates(filled, [row, col], order=1, mode="nearest")
    V = map_coordinates(valid.astype(np.float32), [row, col], order=1, mode="nearest") > 0.5
    Lm = map_coordinates(land.astype(np.float32), [row, col], order=1, mode="nearest") > 0.5
    V &= inside
    Lm &= inside

    def to_px(lon_, lat_):
        x_, y_ = proj.forward(np.array(lon_), np.array(lat_))
        return (x0 + (w / 2 + (x_ - cx) * scale) / SS, y0 + (h / 2 - (y_ - cy) * scale) / SS)

    return np.where(V, H, np.inf).astype(np.float32), Lm, to_px


def frame_hours(n_frames: int, t_end: float) -> list[float]:
    """Ease-in timeline: more screen time for the dense first hours."""
    return [t_end * (i / (n_frames - 1)) ** 1.6 for i in range(n_frames)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Boston")
    ap.add_argument("--format", choices=FORMATS, default="feed")
    args = ap.parse_args()
    (W, Hc), box, title_y = FORMATS[args.format]

    filled, valid, land, lons, lats, step = load_field(args.name)
    proj = Albers()
    H, Lm, to_px = rasterize(proj, filled, valid, land, lons, lats, step, box)
    t_end = float(np.ceil(H[np.isfinite(H)].max()))
    n_bands = int(t_end)
    pal = iso.palette_for(n_bands)[:, :3].astype(np.float32)
    days = math.ceil(n_bands / iso.DAY_HOURS)
    # Video: flat color per driving day. The web map's alternating hour
    # stripes turn into a zebra texture at this scale; day lines carry it.
    for b in range(1, n_bands + 1):
        pal[b] = pal[(b - 1) // iso.DAY_HOURS * iso.DAY_HOURS + 1]

    band = np.where(np.isfinite(H), np.ceil(np.maximum(H, 1e-3)), 0).astype(np.int32)
    band = np.clip(band, 0, n_bands)
    day = np.where(band > 0, (band - 1) // iso.DAY_HOURS, -1)
    day_edge = np.zeros(band.shape, dtype=bool)
    day_edge[:, 1:] |= (day[:, 1:] != day[:, :-1]) & (day[:, 1:] >= 0) & (day[:, :-1] >= 0)
    day_edge[1:, :] |= (day[1:, :] != day[:-1, :]) & (day[1:, :] >= 0) & (day[:-1, :] >= 0)
    day_edge = binary_dilation(day_edge, iterations=SS)
    edge_t = np.where(day_edge, H, np.inf)       # a day line appears once reached

    x0, y0, x1, y1 = box
    base = np.empty((*H.shape, 3), dtype=np.float32)
    base[:] = WATER
    base[Lm] = LAND
    reached_rgb = pal[band]

    origin = (-71.0589, 42.3601)
    cities = {c["name"]: c for c in iso.city_times(
        "http://127.0.0.1:5002", origin, iso.REGIONS["continental"][0])}
    labels = []
    for nm in VIDEO_CITIES:
        c = cities.get(nm)
        if c and c["hours"] is not None:
            px, py = to_px(c["lon"], c["lat"])
            labels.append((c["hours"], nm, float(px), float(py)))
    ox, oy = (float(v) for v in to_px(*origin))

    f_title, f_sub = font("segoeuib.ttf", 46), font("segoeui.ttf", 26)
    f_label, f_small = font("segoeuib.ttf", 21), font("segoeui.ttf", 19)
    f_clock, f_clock2 = font("bahnschrift.ttf", 92), font("bahnschrift.ttf", 40)

    out_dir = iso.OUT
    out_dir.mkdir(exist_ok=True)
    mp4 = out_dir / f"wave_{args.name.lower()}_{args.format}.mp4"
    ff = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{W}x{Hc}", "-r", str(FPS), "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "17", "-preset", "slow",
        "-movflags", "+faststart", str(mp4),
    ], stdin=subprocess.PIPE)

    timeline = frame_hours(WAVE_SECONDS * FPS, t_end)
    timeline += [t_end] * int(HOLD_SECONDS * FPS)
    for i, t in enumerate(timeline):
        rgb = base.copy()
        reached = H <= t
        rgb[reached] = reached_rgb[reached]
        front = reached & (H > t - 0.9)            # glowing leading edge
        glow = (1 - (t - H[front]) / 0.9)[:, None] * 0.75
        rgb[front] = rgb[front] * (1 - glow) + 255 * glow
        rgb[edge_t <= t] = WATER
        img = Image.fromarray(rgb.astype(np.uint8)).reduce(SS)

        canvas = Image.new("RGB", (W, Hc), tuple(int(v) for v in WATER))
        canvas.paste(img, (x0, y0))
        d = ImageDraw.Draw(canvas)
        d.text((40, title_y), f"How far can you drive from {args.name}?", font=f_title, fill=INK)
        d.text((42, title_y + 64),
               f"Drive time from {'Boston City Hall' if args.name == 'Boston' else args.name}"
               " to every road in the US, Canada and Mexico",
               font=f_sub, fill=MUTED)

        # Origin: pulsing dot.
        pulse = 7 + 4 * (0.5 + 0.5 * math.sin(i / FPS * 2 * math.pi * 1.2))
        d.ellipse((ox - pulse, oy - pulse, ox + pulse, oy + pulse), outline=INK, width=2)
        d.ellipse((ox - 4, oy - 4, ox + 4, oy + 4), fill=INK)
        d.text((ox + 14, oy - 14), args.name, font=f_label, fill=INK,
               stroke_width=3, stroke_fill=tuple(int(v) for v in WATER))

        for hrs, nm, px, py in labels:
            if t < hrs:
                continue
            alpha = min(1.0, (t - hrs) / 1.5 + 0.35)
            fill = tuple(int(c * alpha + WATER[k] * (1 - alpha)) for k, c in enumerate(INK))
            text = f"{nm} {hrs:.1f} h"
            tw = d.textlength(text, font=f_label)
            tx = px - 10 - tw if nm in LEFT_LABELS else px + 10
            d.ellipse((px - 4, py - 4, px + 4, py + 4), fill=fill, outline=(13, 17, 23))
            d.text((tx, py - 14), text, font=f_label, fill=fill,
                   stroke_width=3, stroke_fill=tuple(int(v) for v in WATER))

        # Clock + day bar.
        cy = y1 + 30
        cur_day = min(days, int(t // iso.DAY_HOURS) + 1)
        d.text((40, cy), f"DAY {cur_day}", font=f_clock, fill=INK)
        d.text((40 + d.textlength(f"DAY {cur_day}", font=f_clock) + 28, cy + 42),
               f"{t:5.1f} h of driving", font=f_clock2, fill=MUTED)
        bar_y, bar_x0, bar_x1 = cy + 120, 40, W - 40
        seg = (bar_x1 - bar_x0) / days
        for k in range(days):
            col = tuple(int(v) for v in pal[min(k * iso.DAY_HOURS + 1, n_bands)])
            done = t >= k * iso.DAY_HOURS
            d.rectangle((bar_x0 + k * seg + 1, bar_y, bar_x0 + (k + 1) * seg - 1, bar_y + 16),
                        fill=col if done else (40, 46, 58))
        mx = bar_x0 + (bar_x1 - bar_x0) * min(t / (days * iso.DAY_HOURS), 1)
        d.polygon([(mx - 8, bar_y - 12), (mx + 8, bar_y - 12), (mx, bar_y - 2)], fill=INK)
        d.text((40, bar_y + 28), "A day is 8 hours of driving. No traffic, no stops, ferries count.",
               font=f_small, fill=MUTED)
        d.text((40, bar_y + 60),
               "Routed with OSRM on my own server. Map data © OpenStreetMap contributors, Natural Earth.",
               font=f_small, fill=MUTED)

        ff.stdin.write(canvas.tobytes())
        if i % 60 == 0:
            print(f"  frame {i}/{len(timeline)}  t={t:.1f} h", flush=True)
        if i == WAVE_SECONDS * FPS // 2:
            canvas.save(out_dir / f"wave_{args.name.lower()}_{args.format}_mid.png")
    canvas.save(out_dir / f"wave_{args.name.lower()}_{args.format}_final.png")
    ff.stdin.close()
    ff.wait()

    gif = mp4.with_suffix(".gif")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vf",
        "fps=15,scale=540:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=sierra2_4a",
        str(gif),
    ], check=True)
    for p in (mp4, gif):
        print(f"wrote {p.name} ({p.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
