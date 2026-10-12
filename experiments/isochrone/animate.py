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
import flights  # noqa: E402
import isochrone as iso  # noqa: E402  (reuse grid, palette, land mask, cities)

URL = "http://127.0.0.1:5002"
ORIGIN = (-71.0589, 42.3601)
# Where the Hawaii inset sits, in main-map projected units: the empty Pacific
# west of Baja (Hawaii's true position would widen the whole map ~25%).
INSET_XY = (-0.66, -0.20, -0.31, -0.52)            # left, top, right, bottom
# Greenland inset: top right, over its own true position (open water once
# Greenland itself is drawn in the inset instead of the main map).
GREENLAND_XY = (0.37, 0.66, 0.62, 0.40)
BIG_PLANES = {"SEA", "HNL", "MIA", "BDA"}
TRAIL = (120, 128, 142)
TRAIL_FADE = 1.5                                    # hours a trail lingers after landing
MIN_FLIGHT_H = 2.5                                  # ~6 frames at the slow end of the ease
# Video-only labels for places not in isochrone.CITIES (times read off the grid).
EXTRA_CITIES = {"Havana": (-82.3666, 23.1136), "Nassau": (-77.3554, 25.0480),
                "Port of Spain": (-61.5190, 10.6596), "Bermuda": (-64.7814, 32.2949),
                "Nuuk": (-51.7216, 64.1814), "Iqaluit": (-68.5170, 63.7467),
                "Utqiagvik": (-156.7886, 71.2906)}

FONTS = Path("C:/Windows/Fonts")
FPS = 30
WAVE_SECONDS = 28                                    # half speed (was 14 / 2.5)
HOLD_SECONDS = 5
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
    "Monterrey", "Mexico City", "Cancún", "Cabo San Lucas", "Guatemala City",
    "Panama City", "Honolulu", "Hilo", "San Juan",
]
LEFT_LABELS = {"Seattle", "San Francisco", "Los Angeles", "Anchorage", "Key West",
               "Chicago", "St. John's NL", "Miami", "Havana", "Cancún", "San Juan",
               "Iqaluit"}                   # right-side text ran under the Greenland inset

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


def load_field(name: str, origin):
    """Hours everywhere (driving + flights, nearest-road filled) + valid land."""
    hours, lons, lats, step, key, legs = iso.compute_hours(URL, name, origin, "continental")
    land = iso.land_mask(key, "continental", step)
    reachable = ~np.isnan(hours)
    dist, (iy, ix) = distance_transform_edt(~reachable, return_indices=True)
    gap_km = dist * 111.32 * step * np.cos(np.radians(lats))[:, None]
    filled = hours[iy, ix] + gap_km / (iso.LOCAL_SPEED_MS * 3.6)
    valid = land & (gap_km <= iso.FILL_KM)
    return filled.astype(np.float32), valid, land, lons, lats, step, legs


def rasterize(proj, filled, valid, land, lons, lats, step, box, keep):
    """Sample the lon/lat grid onto the projected map box (at SS x).

    `keep` selects which grid cells belong in this box (the main map drops
    Hawaii, the inset keeps only Hawaii); the box is fitted to their extent.
    """
    x0, y0, x1, y1 = box
    w, h = (x1 - x0) * SS, (y1 - y0) * SS
    valid = valid & keep
    land = land & keep
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

    def xy_px(x_, y_):
        """Projected coordinates -> canvas pixels."""
        return (x0 + (w / 2 + (x_ - cx) * scale) / SS, y0 + (h / 2 - (y_ - cy) * scale) / SS)

    def to_px(lon_, lat_):
        return xy_px(*proj.forward(np.array(lon_), np.array(lat_)))

    return np.where(V, H, np.inf).astype(np.float32), Lm, to_px, xy_px


class Layer:
    """One rasterized map (main or inset) that repaints itself for hour t."""

    def __init__(self, H, Lm, pal, n_bands):
        self.H = H
        band = np.where(np.isfinite(H), np.ceil(np.maximum(H, 1e-3)), 0).astype(np.int32)
        band = np.clip(band, 0, n_bands)
        day = np.where(band > 0, (band - 1) // iso.DAY_HOURS, -1)
        edge = np.zeros(band.shape, dtype=bool)
        edge[:, 1:] |= (day[:, 1:] != day[:, :-1]) & (day[:, 1:] >= 0) & (day[:, :-1] >= 0)
        edge[1:, :] |= (day[1:, :] != day[:-1, :]) & (day[1:, :] >= 0) & (day[:-1, :] >= 0)
        self.edge_t = np.where(binary_dilation(edge, iterations=SS), H, np.inf)
        self.base = np.empty((*H.shape, 3), dtype=np.float32)
        self.base[:] = WATER
        self.base[Lm] = LAND
        self.reached_rgb = pal[band]

    def paint(self, t: float) -> Image.Image:
        rgb = self.base.copy()
        reached = self.H <= t
        rgb[reached] = self.reached_rgb[reached]
        front = reached & (self.H > t - 0.9)          # glowing leading edge
        glow = (1 - (t - self.H[front]) / 0.9)[:, None] * 0.75
        rgb[front] = rgb[front] * (1 - glow) + 255 * glow
        rgb[self.edge_t <= t] = WATER
        return Image.fromarray(rgb.astype(np.uint8)).reduce(SS)


# Airplane silhouette, nose toward +x, in ~pixels; mirrored for the other half.
_HALF = [(13, 0), (10, 1.4), (3, 1.7), (-2, 8.5), (-5, 8.5), (-3, 1.7),
         (-8, 1.5), (-10.5, 4.5), (-12.5, 4.5), (-11.5, 0)]
PLANE = _HALF + [(x, -y) for x, y in reversed(_HALF[1:-1])]


def draw_plane(d: ImageDraw.ImageDraw, x: float, y: float, angle: float, scale: float = 1.4):
    c, s = math.cos(angle), math.sin(angle)
    pts = [(x + scale * (px * c - py * s), y + scale * (px * s + py * c)) for px, py in PLANE]
    d.polygon(pts, fill=INK, outline=tuple(int(v) for v in WATER))


def arc(p0, p1, lift: float = 0.22):
    """Quadratic curve between screen points, bowed 'up' like a flight path."""
    (ax, ay), (bx, by) = p0, p1
    mx, my = (ax + bx) / 2, (ay + by) / 2
    dist = math.hypot(bx - ax, by - ay)
    ctrl = (mx, my - lift * dist)

    def at(f: float):
        x = (1 - f) ** 2 * ax + 2 * (1 - f) * f * ctrl[0] + f * f * bx
        y = (1 - f) ** 2 * ay + 2 * (1 - f) * f * ctrl[1] + f * f * by
        dx = 2 * (1 - f) * (ctrl[0] - ax) + 2 * f * (bx - ctrl[0])
        dy = 2 * (1 - f) * (ctrl[1] - ay) + 2 * f * (by - ctrl[1])
        return x, y, math.atan2(dy, dx)
    return at


def frame_hours(n_frames: int, t_end: float) -> list[float]:
    """Ease-in timeline: more screen time for the dense first hours."""
    return [t_end * (i / (n_frames - 1)) ** 1.6 for i in range(n_frames)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Boston")
    ap.add_argument("--format", choices=FORMATS, default="feed")
    args = ap.parse_args()
    (W, Hc), box, title_y = FORMATS[args.format]

    filled, valid, land, lons, lats, step, legs = load_field(args.name, ORIGIN)
    lon_g, lat_g = np.meshgrid(lons, lats)
    def inside(b):
        return (lon_g >= b[0]) & (lon_g <= b[2]) & (lat_g >= b[1]) & (lat_g <= b[3])

    hw, gl = flights.HAWAII, flights.GREENLAND
    in_hawaii, in_greenland = inside(hw), inside(gl)

    H, Lm, to_px, xy_px = rasterize(Albers(), filled, valid, land, lons, lats, step, box,
                                    keep=~in_hawaii & ~in_greenland)

    def screen_box(xy):
        x0_, y0_ = (int(v) for v in xy_px(xy[0], xy[1]))
        x1_, y1_ = (int(v) for v in xy_px(xy[2], xy[3]))
        return (x0_, y0_, x1_, y1_)

    insets = []                      # (box, layer data, to_px, label, region)
    for xy, proj, keep, label, region in (
        (INSET_XY, Albers(lon0=-157.3, lat0=20.5, lat1=19.0, lat2=22.0), in_hawaii, "Hawaii", hw),
        # Greenland inset dropped from the video (Max, 2026-10-12): Greenland
        # stays off the main map too (keep= above), and its flights are cut.
    ):
        if not (valid & keep).any():
            continue
        # Clamp to the map area so an inset never runs into the clock below.
        bx0, by0, bx1, by1 = box
        ibox = tuple(int(v) for v in np.clip(screen_box(xy), [bx0, by0, bx0, by0],
                                             [bx1, by1, bx1, by1]))
        Hi, Lmi, to_px_i, _ = rasterize(proj, filled, valid, land, lons, lats, step, ibox, keep=keep)
        insets.append((ibox, Hi, Lmi, to_px_i, label, region))

    finite = np.concatenate([H[np.isfinite(H)]] + [i[1][np.isfinite(i[1])] for i in insets])
    t_end = float(np.ceil(finite.max()))
    n_bands = int(t_end)
    pal = iso.palette_for(n_bands)[:, :3].astype(np.float32)
    days = math.ceil(n_bands / iso.DAY_HOURS)
    # Video: flat color per driving day. The web map's alternating hour
    # stripes turn into a zebra texture at this scale; day lines carry it.
    for b in range(1, n_bands + 1):
        pal[b] = pal[(b - 1) // iso.DAY_HOURS * iso.DAY_HOURS + 1]
    main_layer = Layer(H, Lm, pal, n_bands)
    inset_layers = [(ibox, Layer(Hi, Lmi, pal, n_bands), label)
                    for ibox, Hi, Lmi, _, label, _ in insets]

    def place(lon, lat):
        """Canvas position, routing inset points (Hawaii, Greenland) into their inset."""
        for _, _, _, to_px_i, _, r in insets:
            if r[0] <= lon <= r[2] and r[1] <= lat <= r[3]:
                return tuple(float(v) for v in to_px_i(lon, lat))
        return tuple(float(v) for v in to_px(lon, lat))

    x0, y0, x1, y1 = box
    origin = ORIGIN
    reach = np.where(valid, filled, np.nan)
    cities = {c["name"]: c for c in iso.city_times(URL, origin, iso.REGIONS["continental"][0])}
    labels = []
    for nm in VIDEO_CITIES:
        c = cities.get(nm)
        if not c:
            continue
        hrs = c["hours"] if c["hours"] is not None else iso.sample(reach, lons, lats, c["lon"], c["lat"])
        if hrs is not None and not (gl[0] <= c["lon"] <= gl[2] and gl[1] <= c["lat"] <= gl[3]):
            labels.append((hrs, nm, *place(c["lon"], c["lat"])))   # no Greenland (no inset)
    for nm, (lon, lat) in EXTRA_CITIES.items():
        hrs = iso.sample(reach, lons, lats, lon, lat)
        if hrs is not None and not (gl[0] <= lon <= gl[2] and gl[1] <= lat <= gl[3]):
            labels.append((hrs, nm, *place(lon, lat)))
    ox, oy = (float(v) for v in to_px(*origin))

    # Flight paths on screen: bowed arcs between the two airports' positions.
    # Draw the full schedule, hub legs included (BOS -> MIA serves no island,
    # so compute_hours doesn't return it).
    if legs:
        legs = flights.plan(iso.drive_to_airports(URL, origin))
    def in_gl(code):
        _, lon, lat = flights.AIRPORTS[code]
        return gl[0] <= lon <= gl[2] and gl[1] <= lat <= gl[3]
    legs = [leg for leg in legs if not (in_gl(leg.src) or in_gl(leg.dst))]
    paths = [(leg, arc(place(*flights.AIRPORTS[leg.src][1:]), place(*flights.AIRPORTS[leg.dst][1:]),
                       lift=0.22 if leg.src == "BOS" else 0.35)) for leg in legs]

    f_title, f_sub = font("segoeuib.ttf", 46), font("segoeui.ttf", 26)
    f_label, f_small = font("segoeuib.ttf", 21), font("segoeui.ttf", 19)
    f_clock, f_clock2 = font("bahnschrift.ttf", 92), font("bahnschrift.ttf", 40)

    # Label positions, fixed once for the whole video (no jumping between
    # frames). Each label scores its candidate spots (preferred side first,
    # then the other side, then vertical nudges) by how much they overlap
    # labels already placed or an inset that isn't theirs, and takes the
    # lowest; ties go to the earlier, more natural spot. (Monterrey ran into
    # Miami, Havana covered Cabo San Lucas, Cabo then slid into Hawaii.)
    def overlap(a, b):
        return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))

    taken = [(ox + 10, oy - 18, ox + 14 + f_label.getlength(args.name) + 4, oy + 14)]
    inset_boxes = [i[0] for i in insets]
    label_xy = {}
    for hrs, nm, px, py in sorted(labels):
        tw = f_label.getlength(f"{nm} {hrs:.1f} h")
        left_first = nm in LEFT_LABELS
        best = None
        for dy in (0, -20, 20, -38, 38, -56, 56):
            for left in (left_first, not left_first):
                tx = px - 10 - tw if left else px + 10
                box = (tx - 3, py - 14 + dy - 2, tx + tw + 3, py - 14 + dy + 27)
                if box[0] < 8 or box[2] > W - 8:
                    continue
                cost = sum(overlap(box, b) for b in taken)
                cost += sum(3 * overlap(box, ib) for ib in inset_boxes
                            if not (ib[0] <= px <= ib[2] and ib[1] <= py <= ib[3]))
                cost += abs(dy) * 0.5          # mild preference for the dot's own line
                if best is None or cost < best[0]:
                    best = (cost, tx, py - 14 + dy, box)
        _, tx, ty, box = best
        taken.append(box)
        label_xy[nm] = (tx, ty)

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
        canvas = Image.new("RGB", (W, Hc), tuple(int(v) for v in WATER))
        canvas.paste(main_layer.paint(t), (x0, y0))
        for ibox, layer, _ in inset_layers:
            canvas.paste(layer.paint(t), ibox[:2])
        d = ImageDraw.Draw(canvas)
        for ibox, _, label in inset_layers:
            d.rectangle(ibox, outline=(70, 78, 92), width=1)
            d.text((ibox[0] + 8, ibox[3] - 28), label, font=f_small, fill=MUTED)
        d.text((40, title_y), f"How far can you drive from {args.name}?", font=f_title, fill=INK)
        d.text((42, title_y + 64),
               f"Drive time from {'Boston City Hall' if args.name == 'Boston' else args.name}."
               " Islands: fly from Logan, then drive.",
               font=f_sub, fill=MUTED)

        # Flights: dashed trail behind the plane while airborne, fading out
        # TRAIL_FADE hours after landing (35+ permanent arcs would bury the map).
        for leg, at in paths:
            # Visual floor: the ease-in timeline gives a 40-minute island hop
            # 2-3 frames, so the plane teleported. The plane and trail take
            # at least MIN_FLIGHT_H of screen time; colors keep the real time.
            arrive = max(leg.arrive, leg.depart + MIN_FLIGHT_H)
            if t < leg.depart or t > arrive + TRAIL_FADE:
                continue
            f = min(1.0, (t - leg.depart) / (arrive - leg.depart))
            fade = 1.0 if t <= arrive else 1 - (t - arrive) / TRAIL_FADE
            col = tuple(int(WATER[k] + (TRAIL[k] - WATER[k]) * fade) for k in range(3))
            pts = [at(f * k / 40)[:2] for k in range(41)]
            for k in range(0, 40, 2):
                d.line([pts[k], pts[k + 1]], fill=col, width=2)
            if t <= arrive:
                x, y, ang = at(f)
                draw_plane(d, x, y, ang, 1.5 if leg.dst in BIG_PLANES else 0.9)

        # Origin: pulsing dot.
        pulse = 7 + 4 * (0.5 + 0.5 * math.sin(i / FPS * 2 * math.pi * 0.6))
        # Dark under-ring: the origin sits on inferno's brightest yellow.
        d.ellipse((ox - pulse - 2, oy - pulse - 2, ox + pulse + 2, oy + pulse + 2),
                  outline=tuple(int(v) for v in WATER), width=2)
        d.ellipse((ox - pulse, oy - pulse, ox + pulse, oy + pulse), outline=INK, width=2)
        d.ellipse((ox - 5, oy - 5, ox + 5, oy + 5), fill=INK, outline=tuple(int(v) for v in WATER))
        d.text((ox + 14, oy - 14), args.name, font=f_label, fill=INK,
               stroke_width=3, stroke_fill=tuple(int(v) for v in WATER))

        for hrs, nm, px, py in labels:
            if t < hrs:
                continue
            alpha = min(1.0, (t - hrs) / 1.5 + 0.35)
            fill = tuple(int(c * alpha + WATER[k] * (1 - alpha)) for k, c in enumerate(INK))
            text = f"{nm} {hrs:.1f} h"
            tx, ty = label_xy[nm]
            if abs(ty - (py - 14)) >= 20:          # nudged off its dot: leader line
                tw = f_label.getlength(text)
                ex = tx - 2 if tx > px else tx + tw + 2
                d.line((px, py, ex, ty + 14), fill=fill, width=2)
            d.ellipse((px - 4, py - 4, px + 4, py + 4), fill=fill, outline=(13, 17, 23))
            d.text((tx, ty), text, font=f_label, fill=fill,
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
        d.text((40, bar_y + 28),
               "A day is 8 hours of driving. No traffic or stops; ferries, borders and flights count.",
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
