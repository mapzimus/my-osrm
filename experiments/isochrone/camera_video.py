"""Camera-driven drive-time video: the view zooms and pans to follow the wave.

DataDork-style (TikTok @boat.patrol): a moving camera that stays on the
action instead of one fixed continental view. Shot list in shots.py; ferry
boats in ferries_anim.py; colours, planes, HUD and projection reused from
animate.py (which still renders the original fixed-view video unchanged).

    python experiments/isochrone/camera_video.py --format reel
    python experiments/isochrone/camera_video.py --format reel --stills 2,8,12,20,28,34,40,44
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import distance_transform_edt, map_coordinates

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import animate as A  # noqa: E402
import ferries_anim as FA  # noqa: E402
import flights  # noqa: E402
import isochrone as iso  # noqa: E402
from shots import Shots  # noqa: E402

# Sharper grids for the close-ups (finest wins where they overlap). Cheap:
# the New England one took 10 s to route.
FINE = [((-77.0, 39.0, -65.0, 47.5), 0.02),          # opening shot
        ((-170.0, 52.0, -148.0, 61.0), 0.02)]        # Alaska Peninsula / Aleutians
EXTRA = {**A.EXTRA_CITIES, "Kodiak": (-152.4072, 57.7900), "Homer": (-151.5483, 59.6425),
         "Cold Bay": (-162.7187, 55.2075)}
# Labels shown only when the view is narrower than this (km): the ferry-chain
# ports crowd the whole-continent finale.
CLOSE_ONLY = {"Kodiak": 3000, "Homer": 3000, "Cold Bay": 3000}
# Fine grids fill roadless land only this far (km) from a road: the
# continental 75 km fill drew round disks over the Alaska Peninsula up close.
FINE_FILL_KM = 20
# Hawaii inset, in map-box pixels (bottom-left of the box).
INSET = (14, 610, 344, 886)
WATER_T = tuple(int(v) for v in A.WATER)


def load_region(name: str, region, fill_km: float = iso.FILL_KM):
    """Hours (driving + flights, nearest-road filled), valid land, land, grid."""
    hours, lons, lats, step, key, legs = iso.compute_hours(A.URL, name, A.ORIGIN, region)
    land = iso.land_mask(key, iso.region_spec(region)[2], step)
    reachable = ~np.isnan(hours)
    dist, (iy, ix) = distance_transform_edt(~reachable, return_indices=True)
    gap_km = dist * 111.32 * step * np.cos(np.radians(lats))[:, None]
    filled = (hours[iy, ix] + gap_km / (iso.LOCAL_SPEED_MS * 3.6)).astype(np.float32)
    valid = land & (gap_km <= fill_km)
    return filled, valid, land, lons, lats, step, legs


class Grid:
    def __init__(self, filled, valid, land, lons, lats, step, drop=None):
        if drop is not None:
            valid, land = valid & ~drop, land & ~drop
        self.filled = filled
        self.code = land.astype(np.float32) + valid.astype(np.float32)   # 0 water, 1 land, 2 reached
        self.lons, self.lats, self.step = lons, lats, step
        self.merc0 = iso.merc_y(float(lats[0]))

    def sample(self, lon, lat, H, C):
        col = (lon - self.lons[0]) / self.step
        row = (self.merc0 - np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))) / math.radians(self.step)
        ins = (col >= 0) & (col <= len(self.lons) - 1) & (row >= 0) & (row <= len(self.lats) - 1)
        if ins.any():
            rc = [row[ins], col[ins]]
            H[ins] = map_coordinates(self.filled, rc, order=1, mode="nearest")
            C[ins] = map_coordinates(self.code, rc, order=1, mode="nearest")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Boston")
    ap.add_argument("--format", choices=A.FORMATS, default="reel")
    ap.add_argument("--stills", help="comma-separated seconds: write PNGs only")
    args = ap.parse_args()
    (W, Hc), box, title_y = A.FORMATS[args.format]
    bx0, by0, bx1, by1 = box
    bw, bh = bx1 - bx0, by1 - by0
    proj = A.Albers()
    t0_wall = time.time()

    # Grids: continental (Hawaii/Greenland drawn elsewhere / not at all) + fine.
    filled, valid, land, lons, lats, step, legs = load_region(args.name, "continental")
    lon_g, lat_g = np.meshgrid(lons, lats)

    def inside(b):
        return (lon_g >= b[0]) & (lon_g <= b[2]) & (lat_g >= b[1]) & (lat_g <= b[3])
    hw, gl = flights.HAWAII, flights.GREENLAND
    in_hawaii = inside(hw)
    grids = [Grid(filled, valid, land, lons, lats, step, drop=in_hawaii | inside(gl))]
    for region in FINE:
        grids.append(Grid(*load_region(args.name, region, FINE_FILL_KM)[:6]))

    # t_end and the whole-continent view, from reached main-map cells.
    main_ok = valid & ~in_hawaii & ~inside(gl)
    t_end = float(np.ceil(np.nanmax(np.where(main_ok, filled, np.nan))))
    ex, ey = proj.forward(lon_g[main_ok], lat_g[main_ok])
    ew, eh = ex.max() - ex.min(), ey.max() - ey.min()
    full = ((ex.max() + ex.min()) / 2, (ey.max() + ey.min()) / 2, max(ew, eh * bw / bh) * 1.03)
    shots = Shots(proj, t_end, full)
    n_bands = int(t_end)
    pal = iso.palette_for(n_bands)[:, :3].astype(np.float32)
    for b in range(1, n_bands + 1):
        pal[b] = pal[(b - 1) // iso.DAY_HOURS * iso.DAY_HOURS + 1]
    days = math.ceil(n_bands / iso.DAY_HOURS)

    # Hawaii inset: rasterized once into its fixed box (map-box pixels).
    Hi, Lmi, to_px_hi, _ = A.rasterize(A.Albers(lon0=-157.3, lat0=20.5, lat1=19.0, lat2=22.0),
                                       filled, valid, land, lons, lats, step, INSET, keep=in_hawaii)
    hawaii_layer = A.Layer(Hi, Lmi, pal, n_bands)

    reach = np.where(valid, filled, np.nan)

    def sample_near(lon, lat, r=4):
        iy, ix = int(np.abs(lats - lat).argmin()), int(np.abs(lons - lon).argmin())
        win = reach[max(iy - r, 0):iy + r + 1, max(ix - r, 0):ix + r + 1]
        ok = np.argwhere(np.isfinite(win))
        if not len(ok):
            return None
        c = np.array([min(iy, r), min(ix, r)])
        y, x = ok[np.abs(ok - c).sum(axis=1).argmin()]
        return float(win[y, x])

    def in_box(b, lon, lat):
        return b[0] <= lon <= b[2] and b[1] <= lat <= b[3]

    ferries = FA.load(sample_near, skip=lambda lon, lat: in_box(hw, lon, lat) or in_box(gl, lon, lat))
    print(f"ferries used by the wave: {len(ferries)}", flush=True)

    # Labels: (hours, name, lon, lat, in_hawaii)
    cities = {c["name"]: c for c in iso.city_times(A.URL, A.ORIGIN, iso.REGIONS["continental"][0])}
    labels = []
    for nm in A.VIDEO_CITIES:
        c = cities.get(nm)
        if c:
            hrs = c["hours"] if c["hours"] is not None else sample_near(c["lon"], c["lat"])
            if hrs is not None and not in_box(gl, c["lon"], c["lat"]):
                labels.append((hrs, nm, c["lon"], c["lat"], in_box(hw, c["lon"], c["lat"])))
    for nm, (lon, lat) in EXTRA.items():
        hrs = sample_near(lon, lat)
        if hrs is not None and not in_box(gl, lon, lat):
            labels.append((hrs, nm, lon, lat, in_box(hw, lon, lat)))
    labels.sort()

    legs = flights.plan(iso.drive_to_airports(A.URL, A.ORIGIN)) if legs else []
    legs = [lg for lg in legs if not (in_box(gl, *flights.AIRPORTS[lg.src][1:])
                                      or in_box(gl, *flights.AIRPORTS[lg.dst][1:]))]

    f_title, f_sub = A.font("segoeuib.ttf", 46), A.font("segoeui.ttf", 26)
    f_label, f_small = A.font("segoeuib.ttf", 21), A.font("segoeui.ttf", 19)
    f_clock, f_clock2 = A.font("bahnschrift.ttf", 92), A.font("bahnschrift.ttf", 40)

    out_dir = iso.OUT
    out_dir.mkdir(exist_ok=True)
    stem = f"camera_{args.name.lower()}_{args.format}"
    if args.stills:
        frames = [round(float(s) * A.FPS) for s in args.stills.split(",")]
        ff = None
    else:
        frames = list(range(int(shots.duration * A.FPS)))
        mp4 = out_dir / f"{stem}.mp4"
        ff = subprocess.Popen([
            "ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{W}x{Hc}", "-r", str(A.FPS), "-i", "-",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "17", "-preset", "slow",
            "-movflags", "+faststart", str(mp4),
        ], stdin=subprocess.PIPE)

    def overlap(a, b):
        return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))

    sticky: dict[str, tuple[bool, int]] = {}
    layer_cache = {"view": None, "layer": None}
    print(f"setup {time.time() - t0_wall:.0f}s; rendering {len(frames)} frames", flush=True)

    for n, i in enumerate(frames):
        s = i / A.FPS
        t = shots.hours(s)
        cx, cy, wu = shots.view(s)
        ha = shots.hawaii(s)
        scale = bw / wu                                  # map-box px per projected unit

        # --- base map for this view
        key = (round(cx, 6), round(cy, 6), round(wu, 6))
        if layer_cache["view"] != key:
            sw, sh = bw * A.SS, bh * A.SS
            xs = cx + (np.arange(sw) + 0.5 - sw / 2) / (scale * A.SS)
            ys = cy - (np.arange(sh) + 0.5 - sh / 2) / (scale * A.SS)
            lon, lat = proj.inverse(*np.meshgrid(xs, ys))
            Hs = np.full(lon.shape, np.inf, dtype=np.float32)
            Cs = np.zeros(lon.shape, dtype=np.float32)
            for g in grids:
                g.sample(lon, lat, Hs, Cs)
            layer_cache = {"view": key, "layer": A.Layer(np.where(Cs > 1.5, Hs, np.inf).astype(np.float32),
                                                         Cs > 0.5, pal, n_bands)}
        mapimg = layer_cache["layer"].paint(t)

        def to_px(lon_, lat_):
            x, y = proj.forward(np.asarray(lon_, dtype=float), np.asarray(lat_, dtype=float))
            return (x - cx) * scale + bw / 2, bh / 2 - (y - cy) * scale

        def place(lon_, lat_):
            if in_box(hw, lon_, lat_) and ha > 0.5:
                return tuple(float(v) for v in to_px_hi(lon_, lat_))
            return tuple(float(v) for v in to_px(lon_, lat_))

        # --- Hawaii inset (fades)
        if ha > 0.01:
            ins = hawaii_layer.paint(t)
            region = mapimg.crop(INSET)
            mapimg.paste(Image.blend(region, ins, ha), INSET[:2])
        d = ImageDraw.Draw(mapimg)
        if ha > 0.01:
            edge = tuple(int(WATER_T[k] + ((70, 78, 92)[k] - WATER_T[k]) * ha) for k in range(3))
            d.rectangle(INSET, outline=edge, width=1)
            d.text((INSET[0] + 8, INSET[3] - 28), "Hawaii", font=f_small,
                   fill=tuple(int(WATER_T[k] + (A.MUTED[k] - WATER_T[k]) * ha) for k in range(3)))

        # --- ferries
        FA.draw(d, ferries, t, to_px, (bw, bh), A.WATER, A.TRAIL, A.INK)

        # --- flights
        for leg in legs:
            arrive = max(leg.arrive, leg.depart + A.MIN_FLIGHT_H)
            if t < leg.depart or t > arrive + A.TRAIL_FADE:
                continue
            src, dst = flights.AIRPORTS[leg.src], flights.AIRPORTS[leg.dst]
            if in_box(hw, *src[1:]) and in_box(hw, *dst[1:]) and ha < 0.5:
                continue                                   # island hop inside a hidden inset
            at = A.arc(place(*src[1:]), place(*dst[1:]), lift=0.22 if leg.src == "BOS" else 0.35)
            f = min(1.0, (t - leg.depart) / (arrive - leg.depart))
            fade = 1.0 if t <= arrive else 1 - (t - arrive) / A.TRAIL_FADE
            col = tuple(int(A.WATER[k] + (A.TRAIL[k] - A.WATER[k]) * fade) for k in range(3))
            pts = [at(f * k / 40)[:2] for k in range(41)]
            for k in range(0, 40, 2):
                d.line([pts[k], pts[k + 1]], fill=col, width=2)
            if t <= arrive:
                x, y, ang = at(f)
                A.draw_plane(d, x, y, ang, 1.5 if leg.dst in A.BIG_PLANES else 0.9)

        # --- origin
        ox, oy = place(*A.ORIGIN)
        pulse = 7 + 4 * (0.5 + 0.5 * math.sin(i / A.FPS * 2 * math.pi * 0.6))
        d.ellipse((ox - pulse - 2, oy - pulse - 2, ox + pulse + 2, oy + pulse + 2), outline=WATER_T, width=2)
        d.ellipse((ox - pulse, oy - pulse, ox + pulse, oy + pulse), outline=A.INK, width=2)
        d.ellipse((ox - 5, oy - 5, ox + 5, oy + 5), fill=A.INK, outline=WATER_T)
        nw = f_label.getlength(args.name)
        nx = ox + 14 if ox + 14 + nw < bw - 4 else ox - 14 - nw   # flip left at the edge
        d.text((nx, oy - 14), args.name, font=f_label, fill=A.INK, stroke_width=3, stroke_fill=WATER_T)

        # --- labels: least-overlap layout per frame, sticky to last frame's spot
        taken = [(min(nx, ox) - 4, oy - 18, max(nx + nw, ox) + 4, oy + 14)]
        inset_live = ha > 0.05
        for hrs, nm, lon, lat, hi in labels:
            if t < hrs or (hi and not inset_live) or wu * 6371 > CLOSE_ONLY.get(nm, 1e9):
                continue
            px, py = place(lon, lat)
            if not (0 <= px <= bw and 0 <= py <= bh):
                sticky.pop(nm, None)
                continue
            text = f"{nm} {hrs:.1f} h"
            tw = f_label.getlength(text)
            left_first = nm in A.LEFT_LABELS
            best = None
            for dy in (0, -20, 20, -38, 38, -56, 56):
                for left in (left_first, not left_first):
                    tx = px - 10 - tw if left else px + 10
                    bb = (tx - 3, py - 14 + dy - 2, tx + tw + 3, py - 14 + dy + 27)
                    if bb[0] < 4 or bb[2] > bw - 4 or bb[1] < 2 or bb[3] > bh - 2:
                        continue
                    cost = sum(overlap(bb, o) for o in taken) + abs(dy) * 0.5
                    if inset_live and not hi:
                        cost += 3 * overlap(bb, INSET)
                    if sticky.get(nm) != (left, dy):
                        cost += 40                      # stickiness: don't flip mid-zoom
                    if best is None or cost < best[0]:
                        best = (cost, left, dy, tx, bb)
            if best is None:
                continue
            _, left, dy, tx, bb = best
            sticky[nm] = (left, dy)
            taken.append(bb)
            alpha = min(1.0, (t - hrs) / 1.5 + 0.35) * (ha if hi else 1.0)
            fill = tuple(int(c * alpha + A.WATER[k] * (1 - alpha)) for k, c in enumerate(A.INK))
            ty = py - 14 + dy
            if abs(dy) >= 20:
                ex_ = tx - 2 if not left else tx + tw + 2
                d.line((px, py, ex_, ty + 14), fill=fill, width=2)
            d.ellipse((px - 4, py - 4, px + 4, py + 4), fill=fill, outline=WATER_T)
            d.text((tx, ty), text, font=f_label, fill=fill, stroke_width=3, stroke_fill=WATER_T)

        # --- canvas: title, map, HUD (unchanged from animate.py)
        canvas = Image.new("RGB", (W, Hc), WATER_T)
        canvas.paste(mapimg, (bx0, by0))
        dc = ImageDraw.Draw(canvas)
        dc.text((40, title_y), f"How far can you drive from {args.name}?", font=f_title, fill=A.INK)
        dc.text((42, title_y + 64),
                f"Drive time from {'Boston City Hall' if args.name == 'Boston' else args.name}."
                " Islands: fly from Logan, then drive.", font=f_sub, fill=A.MUTED)
        hy = by1 + 30
        cur_day = min(days, int(t // iso.DAY_HOURS) + 1)
        dc.text((40, hy), f"DAY {cur_day}", font=f_clock, fill=A.INK)
        dc.text((40 + dc.textlength(f"DAY {cur_day}", font=f_clock) + 28, hy + 42),
                f"{t:5.1f} h of driving", font=f_clock2, fill=A.MUTED)
        bar_y, bar_x0, bar_x1 = hy + 120, 40, W - 40
        seg = (bar_x1 - bar_x0) / days
        for k in range(days):
            col = tuple(int(v) for v in pal[min(k * iso.DAY_HOURS + 1, n_bands)])
            dc.rectangle((bar_x0 + k * seg + 1, bar_y, bar_x0 + (k + 1) * seg - 1, bar_y + 16),
                         fill=col if t >= k * iso.DAY_HOURS else (40, 46, 58))
        mx = bar_x0 + (bar_x1 - bar_x0) * min(t / (days * iso.DAY_HOURS), 1)
        dc.polygon([(mx - 8, bar_y - 12), (mx + 8, bar_y - 12), (mx, bar_y - 2)], fill=A.INK)
        dc.text((40, bar_y + 28),
                "A day is 8 hours of driving. No traffic or stops; ferries, borders and flights count.",
                font=f_small, fill=A.MUTED)
        dc.text((40, bar_y + 60),
                "Routed with OSRM on my own server. Map data © OpenStreetMap contributors, Natural Earth.",
                font=f_small, fill=A.MUTED)

        if ff is None:
            p = out_dir / f"{stem}_{s:04.1f}s.png"
            canvas.save(p)
            print(f"  still {p.name}  t={t:.1f} h", flush=True)
        else:
            ff.stdin.write(canvas.tobytes())
            if n % 60 == 0:
                el = time.time() - t0_wall
                print(f"  frame {n}/{len(frames)}  t={t:.1f} h  {el / 60:.1f} min", flush=True)
            if n == len(frames) - 1:
                canvas.save(out_dir / f"{stem}_final.png")

    if ff is not None:
        ff.stdin.close()
        ff.wait()
        gif = mp4.with_suffix(".gif")
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vf",
            "fps=15,scale=540:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];"
            "[b][p]paletteuse=dither=sierra2_4a", str(gif)], check=True)
        for p in (mp4, gif):
            print(f"wrote {p.name} ({p.stat().st_size / 1e6:.1f} MB)")
    print(f"total {(time.time() - t0_wall) / 60:.1f} min")


if __name__ == "__main__":
    main()
