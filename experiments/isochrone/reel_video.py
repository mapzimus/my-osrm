"""The Boston drive-time video in the Optimal Drives house style, one export
per platform (TikTok, Instagram Reels).

Same wave, camera path, ferries and flights as camera_video.py (which still
renders the original reel/feed cut unchanged); the layout comes from
reel_kit: map behind the whole frame, HUD in the platform's safe zone, the
answer as a hook on frame 1, a "now" label, an end card and a soundtrack.

    python experiments/isochrone/reel_video.py --platform tiktok
    python experiments/isochrone/reel_video.py --platform reels --stills 0,6,14,30,46
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import animate as A  # noqa: E402
import camera_video as CV  # noqa: E402
import ferries_anim as FA  # noqa: E402
import flights  # noqa: E402
import isochrone as iso  # noqa: E402
import reel_kit as K  # noqa: E402
from shots import HAWAII as SHOT_HAWAII, KEYS as SHOT_KEYS, Shots  # noqa: E402

NAME = "Boston"
SCALE_W = 1020          # the shot list's km widths were framed for a 1020 px box
SECONDS = 65.0          # house length (doc §2); the shot list is stretched to fit
STRETCH = (SECONDS - 0.6 - K.END_CARD + 2.5) / SHOT_KEYS[-1][0]
CARD_IN = SHOT_KEYS[-1][0] * STRETCH - 2.5     # end card fades in as the finale view settles
WATER_T = K.WATER

C: dict = {}            # per-process state, filled by setup()


def setup(platform: str) -> None:
    K.use(platform)
    C.clear()
    proj = A.Albers()
    filled, valid, land, lons, lats, step, legs = CV.load_region(NAME, "continental")
    lon_g, lat_g = np.meshgrid(lons, lats)

    def inside(b):
        return (lon_g >= b[0]) & (lon_g <= b[2]) & (lat_g >= b[1]) & (lat_g <= b[3])
    hw, gl = flights.HAWAII, flights.GREENLAND
    in_hawaii = inside(hw)
    grids = [CV.Grid(filled, valid, land, lons, lats, step, drop=in_hawaii | inside(gl))]
    for region in CV.FINE:
        grids.append(CV.Grid(*CV.load_region(NAME, region, CV.FINE_FILL_KM)[:6]))

    # The whole-continent view is fitted to the map band between the HUD and
    # the "now" label; the map itself runs on behind both.
    band_h = K.MAP_BOTTOM - K.MAP_TOP
    main_ok = valid & ~in_hawaii & ~inside(gl)
    t_end = float(np.ceil(np.nanmax(np.where(main_ok, filled, np.nan))))
    ex, ey = proj.forward(lon_g[main_ok], lat_g[main_ok])
    ew, eh = ex.max() - ex.min(), ey.max() - ey.min()
    full = ((ex.max() + ex.min()) / 2, (ey.max() + ey.min()) / 2, max(ew, eh * SCALE_W / band_h) * 1.03)
    shots = Shots(proj, t_end, full, keys=[(k[0] * STRETCH, *k[1:]) for k in SHOT_KEYS],
                  hawaii=[(h[0] * STRETCH, h[1]) for h in SHOT_HAWAII])
    n_bands = int(t_end)
    pal = iso.palette_for(n_bands)[:, :3].astype(np.float32)
    for b in range(1, n_bands + 1):
        pal[b] = pal[(b - 1) // iso.DAY_HOURS * iso.DAY_HOURS + 1]

    # Hawaii inset: bottom left of the map band, clear of the right rail.
    inset = (14, K.MAP_BOTTOM - 290, 344, K.MAP_BOTTOM - 14)
    Hi, Lmi, to_px_hi, _ = A.rasterize(A.Albers(lon0=-157.3, lat0=20.5, lat1=19.0, lat2=22.0),
                                       filled, valid, land, lons, lats, step, inset, keep=in_hawaii)

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
    cities = {c["name"]: c for c in iso.city_times(A.URL, A.ORIGIN, iso.REGIONS["continental"][0])}
    labels = []
    for nm in A.VIDEO_CITIES:
        c = cities.get(nm)
        if c:
            hrs = c["hours"] if c["hours"] is not None else sample_near(c["lon"], c["lat"])
            if hrs is not None and not in_box(gl, c["lon"], c["lat"]):
                labels.append((hrs, nm, c["lon"], c["lat"], in_box(hw, c["lon"], c["lat"])))
    for nm, (lon, lat) in CV.EXTRA.items():
        hrs = sample_near(lon, lat)
        if hrs is not None and not in_box(gl, lon, lat):
            labels.append((hrs, nm, lon, lat, in_box(hw, lon, lat)))
    labels.sort()
    legs = flights.plan(iso.drive_to_airports(A.URL, A.ORIGIN)) if legs else []
    legs = [lg for lg in legs if not (in_box(gl, *flights.AIRPORTS[lg.src][1:])
                                      or in_box(gl, *flights.AIRPORTS[lg.dst][1:]))]
    C.update(proj=proj, grids=grids, shots=shots, t_end=t_end, n_bands=n_bands, pal=pal,
             days=math.ceil(n_bands / iso.DAY_HOURS), inset=inset, to_px_hi=to_px_hi,
             hawaii=A.Layer(Hi, Lmi, pal, n_bands), ferries=ferries, labels=labels, legs=legs,
             in_box=in_box, hw=hw, sticky={}, cache={"view": None})


def overlap(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def label_box_ok(bb) -> bool:
    """Inside the map band, and clear of the right rail in the lower half."""
    right = K.W - 4 if bb[3] < 900 else K.W - K.RIGHT_RAIL
    return bb[0] >= 4 and bb[2] <= right and bb[1] >= K.MAP_TOP + 50 and bb[3] <= K.MAP_BOTTOM - 4


def map_layer(s: float, t: float):
    """The full-frame map for video second s; returns (image, to_px, scale)."""
    proj, shots = C["proj"], C["shots"]
    cx, cy, wu = shots.view(s)
    scale = SCALE_W / wu
    key = (round(cx, 6), round(cy, 6), round(wu, 6))
    if C["cache"]["view"] != key:
        sw, sh = K.W * A.SS, K.H * A.SS
        cyp = K.MAP_CENTER_Y * A.SS                   # the view centre sits mid-band
        xs = cx + (np.arange(sw) + 0.5 - sw / 2) / (scale * A.SS)
        ys = cy - (np.arange(sh) + 0.5 - cyp) / (scale * A.SS)
        lon, lat = proj.inverse(*np.meshgrid(xs, ys))
        Hs = np.full(lon.shape, np.inf, dtype=np.float32)
        Cs = np.zeros(lon.shape, dtype=np.float32)
        for g in C["grids"]:
            g.sample(lon, lat, Hs, Cs)
        C["cache"] = {"view": key, "layer": A.Layer(np.where(Cs > 1.5, Hs, np.inf).astype(np.float32),
                                                    Cs > 0.5, C["pal"], C["n_bands"])}

    def to_px(lon_, lat_):
        x, y = proj.forward(np.asarray(lon_, dtype=float), np.asarray(lat_, dtype=float))
        return (x - cx) * scale + K.W / 2, K.MAP_CENTER_Y - (y - cy) * scale
    return C["cache"]["layer"].paint(t), to_px, wu


def now_state(t: float):
    """Kicker, name, place for the "now" band: a flight in the air, else the
    last labelled place reached."""
    for leg in C["legs"]:
        if leg.depart <= t <= max(leg.arrive, leg.depart + A.MIN_FLIGHT_H):
            return "Flight", f"{leg.src} → {leg.dst}", ""
    done = [lb for lb in C["labels"] if lb[0] <= t]
    if not done:
        return "Leaving", NAME, "every road, every direction"
    hrs, nm = done[-1][:2]
    return "Just reached", nm, f"{hrs:.1f} h from {NAME}"


def frame(i: int) -> bytes:
    s = i / K.FPS
    shots, in_box, hw, inset = C["shots"], C["in_box"], C["hw"], C["inset"]
    t = shots.hours(s)
    ha = shots.hawaii(s)
    mapimg, to_px, wu = map_layer(s, t)

    def place(lon_, lat_):
        if in_box(hw, lon_, lat_) and ha > 0.5:
            return tuple(float(v) for v in C["to_px_hi"](lon_, lat_))
        return tuple(float(v) for v in to_px(lon_, lat_))

    if ha > 0.01:
        region = mapimg.crop(inset)
        mapimg.paste(Image.blend(region, C["hawaii"].paint(t), ha), inset[:2])
    d = ImageDraw.Draw(mapimg)
    f_label, f_small = A.font("segoeuib.ttf", 21), A.font("segoeui.ttf", 19)
    if ha > 0.01:
        edge = tuple(int(WATER_T[k] + ((70, 78, 92)[k] - WATER_T[k]) * ha) for k in range(3))
        d.rectangle(inset, outline=edge, width=1)
        d.text((inset[0] + 8, inset[3] - 28), "Hawaii", font=f_small,
               fill=tuple(int(WATER_T[k] + (A.MUTED[k] - WATER_T[k]) * ha) for k in range(3)))

    FA.draw(d, C["ferries"], t, to_px, (K.W, K.H), A.WATER, A.TRAIL, A.INK)

    for leg in C["legs"]:
        arrive = max(leg.arrive, leg.depart + A.MIN_FLIGHT_H)
        if t < leg.depart or t > arrive + A.TRAIL_FADE:
            continue
        src, dst = flights.AIRPORTS[leg.src], flights.AIRPORTS[leg.dst]
        if in_box(hw, *src[1:]) and in_box(hw, *dst[1:]) and ha < 0.5:
            continue
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

    # origin
    ox, oy = place(*A.ORIGIN)
    pulse = 7 + 4 * (0.5 + 0.5 * math.sin(s * 2 * math.pi * 0.6))
    d.ellipse((ox - pulse - 2, oy - pulse - 2, ox + pulse + 2, oy + pulse + 2), outline=WATER_T, width=2)
    d.ellipse((ox - pulse, oy - pulse, ox + pulse, oy + pulse), outline=A.INK, width=2)
    d.ellipse((ox - 5, oy - 5, ox + 5, oy + 5), fill=A.INK, outline=WATER_T)
    nw = f_label.getlength(NAME)
    nx = ox + 14 if ox + 14 + nw < K.W - 4 else ox - 14 - nw
    d.text((nx, oy - 14), NAME, font=f_label, fill=A.INK, stroke_width=3, stroke_fill=WATER_T)

    # labels (camera_video's least-overlap layout, kept inside the map band)
    sticky = C["sticky"]
    taken = [(min(nx, ox) - 4, oy - 18, max(nx + nw, ox) + 4, oy + 14)]
    inset_live = ha > 0.05
    for hrs, nm, lon, lat, hi in C["labels"]:
        if t < hrs or (hi and not inset_live) or wu * 6371 > CV.CLOSE_ONLY.get(nm, 1e9):
            continue
        px, py = place(lon, lat)
        if not (0 <= px <= K.W and K.MAP_TOP <= py <= K.MAP_BOTTOM):
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
                if not label_box_ok(bb):
                    continue
                cost = sum(overlap(bb, o) for o in taken) + abs(dy) * 0.5
                if inset_live and not hi:
                    cost += 3 * overlap(bb, inset)
                if sticky.get(nm) != (left, dy):
                    cost += 40
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

    # --- HUD (reel_kit layout)
    im = mapimg.convert("RGBA")
    card_a = K.smoothstep((s - CARD_IN) / 0.6)
    if card_a > 0:                                  # dim the map behind the end card
        im.alpha_composite(Image.new("RGBA", im.size, (*WATER_T, int(190 * card_a))))
    im.alpha_composite(C.setdefault("shade", K.shade()))
    d = ImageDraw.Draw(im)
    T = K.TOP
    d.text((K.W / 2, T), K.spaced(K.SERIES.upper()), font=K.F("tag"), fill=K.ACCENT, anchor="mt")
    d.text((K.W / 2, T + 34), f"How far can you drive from {NAME}?", font=K.F("title"), fill=K.INK, anchor="mt")
    sub = K.font("segoeui.ttf", 24)
    d.text((K.W / 2, T + 102), f"Drive time from {NAME} to all of North America.", font=sub,
           fill=K.MUTED, anchor="mt")
    d.text((K.W / 2, T + 132), "Places inaccessible by road are flown to and then calculated.", font=sub,
           fill=K.MUTED, anchor="mt")
    days = C["days"]
    cur_day = min(days, int(t // iso.DAY_HOURS) + 1)
    K.stats(d, [(K.clock(t), "hours of driving", True), (str(cur_day), f"driving day of {days}", False)])
    # Day bar in the chips row: one segment per 8-hour day, in the map's colours.
    pal, n_bands = C["pal"], C["n_bands"]
    bar_y, x0, x1 = T + 350, 36, K.W - 36
    seg = (x1 - x0) / days
    for k in range(days):
        col = tuple(int(v) for v in pal[min(k * iso.DAY_HOURS + 1, n_bands)])
        d.rectangle((x0 + k * seg + 2, bar_y, x0 + (k + 1) * seg - 2, bar_y + 18),
                    fill=col if t >= k * iso.DAY_HOURS else K.CHIP_FILL)
    mx = x0 + (x1 - x0) * min(t / (days * iso.DAY_HOURS), 1)
    d.polygon([(mx - 9, bar_y - 14), (mx + 9, bar_y - 14), (mx, bar_y - 3)], fill=K.INK)
    d.text((K.W / 2, bar_y + 30), "one block = one day of 8 h driving · no traffic or stops",
           font=K.font("segoeui.ttf", 20), fill=K.MUTED, anchor="mt")
    K.watermark(d)

    if card_a < 1:
        K.now_label(d, *now_state(t))

    hook_a = 1 - K.smoothstep((s - 1.8) / 0.8)
    K.hook(im, [f"{C['t_end']:.0f} hours", f"from {NAME}"], "to every road in North America", hook_a,
           center_y=K.TEXT_FLOOR - 330)

    if card_a > 0:
        lab = {nm: h for h, nm, *_ in C["labels"]}
        rows = [(f"{C['t_end']:.0f} h", "to the farthest road: the Aleutians, AK"),
                (f"{days} days", "driving 8 hours a day"),
                (f"{lab['Los Angeles']:.0f} h", f"{NAME} to Los Angeles"),
                (f"{lab['Panama City']:.0f} h", f"{NAME} to Panama City")]
        K.end_card(im, rows, ["No traffic or stops. Ferries, borders and flights count.",
                              "OSRM on OpenStreetMap · Natural Earth"], card_a)
    return im.convert("RGB").tobytes()


def init(platform: str) -> None:
    setup(platform)


def sound(path: Path) -> None:
    """A tick per labelled place, a chime per finished day, a chord at the end."""
    shots, labels = C["shots"], C["labels"]
    secs = np.arange(0, shots.duration, 1 / K.FPS)
    hrs = np.array([shots.hours(s) for s in secs])
    when = lambda h: float(secs[np.searchsorted(hrs, h)]) if h <= hrs[-1] else None  # noqa: E731
    ticks = [w for w in (when(lb[0]) for lb in labels) if w is not None and w > 2.0]
    chimes = [w for w in (when(k * iso.DAY_HOURS) for k in range(1, C["days"])) if w is not None]
    K.soundtrack(path, SECONDS, ticks, chimes, when(C["t_end"] - 0.01))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["tiktok", "reels"], default="tiktok")
    ap.add_argument("--stills", help="comma-separated seconds: write PNGs only")
    ap.add_argument("--workers", type=int, default=1)
    # Off by default: Max didn't like the dings (2026-10-13); add app audio when posting.
    ap.add_argument("--sound", action="store_true", help="ticks, day chimes and a final chord")
    args = ap.parse_args()
    t0 = time.time()
    out = iso.OUT
    out.mkdir(exist_ok=True)
    stem = f"drivetime_{NAME.lower()}_{args.platform}"
    if args.stills:
        setup(args.platform)
        for v in args.stills.split(","):
            i = round(float(v) * K.FPS)
            C["sticky"].clear()
            Image.frombytes("RGB", (K.W, K.H), frame(i)).save(out / f"{stem}_{float(v):04.1f}s.png")
            print(f"still {v}s  {time.time() - t0:.0f}s", flush=True)
        return
    setup(args.platform)
    n = int(SECONDS * K.FPS)
    silent, wav, mp4 = out / f"{stem}_silent.mp4", out / f"{stem}.wav", out / f"{stem}.mp4"
    if args.sound:
        sound(wav)
    ff = K.encoder(silent)
    # Contiguous chunks so each worker's layout cache and sticky labels stay coherent.
    # One worker renders in this process: each worker holds ~1.5 GB, and on a
    # busy machine (2026-10-13: 0.5 GB free) four of them swapped for 20 min.
    pool = Pool(args.workers, initializer=init, initargs=(args.platform,)) if args.workers > 1 else None
    bufs = pool.imap(frame, range(n), chunksize=30) if pool else map(frame, range(n))
    for k, buf in enumerate(bufs):
        ff.stdin.write(buf)
        if k % 150 == 0:
            print(f"  frame {k}/{n}  {(time.time() - t0) / 60:.1f} min", flush=True)
    if pool:
        pool.close()
    ff.stdin.close()
    ff.wait()
    if args.sound:
        K.mux(silent, wav, mp4)
        silent.unlink()
        wav.unlink()
    else:
        silent.replace(mp4)
    print(f"wrote {mp4.name} ({mp4.stat().st_size / 1e6:.1f} MB) in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
