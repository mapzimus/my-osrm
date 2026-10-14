"""Turf Wars: Dunkin' vs. Starbucks, the 65 s social cut (TikTok and Reels).

Every store leaves at once; each road lights up in the colour of the chain
that reaches it first (draw.py). House layout from experiments/reel_kit.py
(safe zones per platform, hook, HUD, now label, end card), branded Turf Wars.

    python experiments/dunkin-vs-starbucks/reel.py --platform tiktok --stills 1,6,18,28,38,55,63
    python experiments/dunkin-vs-starbucks/reel.py --platform tiktok
    python experiments/dunkin-vs-starbucks/reel.py --platform reels

Writes out/turf_wars_<platform>.mp4 (silent; add audio in the app).

Every number on screen is from cache/ (stores.csv, drivetime.npz); the city
splits are the share of major-road length within CITY_KM of the centre.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import reel_kit as K  # noqa: E402
from draw import COLOURS, OUT, Canvas, albers, load, pieces  # noqa: E402

K.SERIES = "Turf Wars"
K.WATER = (0, 0, 0)                     # black map, as in the still
K.ACCENT = K.INK                        # amber is Optimal Drives' accent; Turf Wars' colours are the chains
FPS, W, H = K.FPS, K.W, K.H
SECONDS = 65
RACE_START = K.HOOK + K.DIVE            # the clock starts at 0:00 here
CARD_IN = SECONDS - K.END_CARD - 0.8
RACE_END = CARD_IN - K.PULL_OUT         # the clock reaches the slowest road here
CLOCK_SHARE = 0.4
FADE_S = 0.5                            # the hook's finished map fades out
DOT_DIM = (120.0, 1200.0, 0.3)          # store dots: full until 0:02 of drive time, down to 30% by 0:20
CITY_KM = 40
TEXT = {0: COLOURS[0], 1: (40, 170, 120)}   # Starbucks text a shade lighter than the map green: readable on black

# Views: (lon, lat, frame width km) or "US" (the lower 48 in the map area).
VIEWS = {"Boston": (-71.06, 42.36, 110), "Northeast": (-73.6, 41.3, 650), "DC": (-76.9, 39.1, 220),
         "Chicago": (-87.85, 41.85, 230), "Seattle": (-122.3, 47.55, 150), "US": None}
# (second, view): hold where two keys name the same view, move (smoothstep, zoom out and in) between.
SHOTS = [(RACE_START, "Boston"), (9.0, "Boston"), (12.5, "Northeast"), (14.0, "Northeast"), (17.0, "DC"),
         (22.5, "DC"), (26.0, "Chicago"), (31.0, "Chicago"), (35.5, "Seattle"), (40.5, "Seattle"), (45.5, "US")]
# Now label per held view: (kicker, line, place line). Splits filled in from the data.
LABELS = {"Boston": ("Boston", "{d}% of roads: Dunkin'", "Dunkin' started in Quincy, MA, 1950"),
          "Northeast": ("The Northeast", "Dunkin' country", "New York {d_ny}% · Philadelphia {d_ph}% Dunkin'"),
          "DC": ("The frontline", "Washington–Baltimore: {d}% / {s}%", "Dunkin' / Starbucks, the closest split"),
          "Chicago": ("Chicago", "{d}% of roads: Dunkin'", "a Dunkin' island in the Midwest"),
          "Seattle": ("Seattle", "{s}% of roads: Starbucks", "where Starbucks started, 1971")}
CITIES = {"Boston": (-71.06, 42.36), "DC": (-76.9, 39.1), "Chicago": (-87.7, 41.88), "Seattle": (-122.33, 47.6),
          "ny": (-73.99, 40.73), "ph": (-75.16, 39.95)}


# --- data -------------------------------------------------------------------------

def prepare():
    pc = pieces(load())
    order = np.argsort(pc["t"], kind="stable")
    ts = pc["t"][order]
    length = np.hypot(*(pc["p1"] - pc["p0"]).T).astype(np.float64)     # float32 cumsum drifts ~2% over 14.6 M pieces
    mx, my = ((pc["p0"] + pc["p1"]) / 2).T
    total = float(length.sum())
    cum = [np.concatenate([[0.0], np.cumsum(np.where(pc["chain"][order] == c, length[order], 0.0))]) / total
           for c in (0, 1)]
    split = {}
    for name, (lo, la) in CITIES.items():
        x, y = albers([lo], [la])
        m = np.hypot(mx - x[0], my - y[0]) < CITY_KM
        d = length[m & (pc["chain"] == 0)].sum() / length[m].sum()
        split[name] = (round(100 * d), round(100 * (1 - d)))
    last = order[-1]
    sx, sy, sc = store_points()
    return dict(sx=sx, sy=sy, sc=sc, pc=pc, order=order, ts=ts, cum=cum, mx=mx, my=my, split=split,
                share=(float(cum[0][-1]), float(cum[1][-1])), last=last,
                stores=store_counts(), t_fade=float(np.percentile(ts, 99.5)))


def store_points():
    import csv
    lon, lat, ch = [], [], []
    with open(HERE / "cache" / "stores.csv", encoding="utf-8") as fh:
        for s in csv.DictReader(fh):
            lon.append(float(s["lon"]))
            lat.append(float(s["lat"]))
            ch.append(0 if s["chain"] == "dunkin" else 1)
    x, y = albers(lon, lat)
    return x, y, np.array(ch)


def store_counts() -> dict:
    import csv
    n = {"dunkin": 0, "starbucks": 0}
    with open(HERE / "cache" / "stores.csv", encoding="utf-8") as fh:
        for s in csv.DictReader(fh):
            n[s["chain"]] += 1
    return n


def schedule(ts: np.ndarray, n: int) -> np.ndarray:
    """Drive time (s) for n race frames: a blend of the clock and the share of roads reached runs linearly."""
    t_end = float(ts[-1])
    grid = np.linspace(0.0, t_end, 40_000)
    progress = CLOCK_SHARE * grid / t_end + (1 - CLOCK_SHARE) * np.searchsorted(ts, grid, side="right") / len(ts)
    return np.interp(np.linspace(0.0, 1.0, n), progress, grid)


# --- camera -----------------------------------------------------------------------

def us_view(top: float, bottom: float, pc) -> tuple[float, float, float]:
    """(centre x, centre y, km per px) fitting the lower 48 between screen rows top and bottom."""
    x0, y0, x1, y1 = pc["bounds"]
    k = max((x1 - x0) * 1.04 / W, (y1 - y0) * 1.04 / (bottom - top))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return cx, cy + ((top + bottom) / 2 - K.MAP_CENTER_Y) * k, k      # view centre is drawn at MAP_CENTER_Y


def view_of(name: str, pc) -> tuple[float, float, float]:
    if VIEWS[name] is None:
        return us_view(K.MAP_TOP, K.MAP_BOTTOM, pc)
    lon, lat, width = VIEWS[name]
    x, y = albers([lon], [lat])
    return float(x[0]), float(y[0]), width / W


def move(a, b, k: float) -> tuple[float, float, float]:
    """Smoothstep from view a to b; long moves zoom out on the way so both ends stay in sight."""
    k = k * k * (3 - 2 * k)
    la, lb = math.log(a[2]), math.log(b[2])
    need = math.hypot(b[0] - a[0], b[1] - a[1]) / (W * 0.8)              # km per px to see both ends
    bump = max(0.0, math.log(max(need, 1e-9)) - (la + lb) / 2)
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, math.exp(la + (lb - la) * k + bump * 4 * k * (1 - k)))


def camera(sec: float, pc, card_view) -> tuple[tuple, str | None]:
    """View at a video second and the name of the view being held (None while moving)."""
    if sec < K.HOOK:
        return view_of("US", pc), "hook"
    if sec < RACE_START:
        return move(view_of("US", pc), view_of(SHOTS[0][1], pc), (sec - K.HOOK) / K.DIVE), None
    if sec > RACE_END:
        return move(view_of(SHOTS[-1][1], pc), card_view, min(1.0, (sec - RACE_END) / K.PULL_OUT)), None
    for (s0, v0), (s1, v1) in zip(SHOTS, SHOTS[1:]):
        if sec <= s1:
            if v0 == v1:
                return view_of(v0, pc), v0
            return move(view_of(v0, pc), view_of(v1, pc), (sec - s0) / (s1 - s0)), None
    return view_of(SHOTS[-1][1], pc), SHOTS[-1][1]


def canvas_for(view) -> Canvas:
    x, y, k = view
    cv = Canvas((0, 0, 1, 1), W, H)                        # transform set below: (x, y) at (W/2, MAP_CENTER_Y)
    cv.s, cv.ox, cv.oy = 1 / k, W / 2 - x / k, K.MAP_CENTER_Y + y / k
    return cv


# --- frames -----------------------------------------------------------------------

class Reel:
    def __init__(self, platform: str):
        K.use(platform)
        self.d = prepare()
        pc = self.d["pc"]
        n = SECONDS * FPS
        self.secs = np.arange(n) / FPS
        r0, r1 = int(RACE_START * FPS), int(RACE_END * FPS)
        drive = np.zeros(n)
        drive[r0:r1 + 1] = schedule(self.d["ts"], r1 + 1 - r0)
        drive[r1 + 1:] = self.d["ts"][-1]
        self.drive = drive
        self.upto = np.searchsorted(self.d["ts"], drive, side="right")
        self.upto[:r0] = 0
        self.card_bottom = end_card(Image.new("RGBA", (1, 1)), *self.end_rows(), 0)
        self.card_view = us_view(self.card_bottom + 30, K.TEXT_FLOOR, pc)
        self.shade = K.shade()
        self.cv, self.view, self.done = None, None, 0
        self.hook_img = None
        self.final = None                                     # finished map at the US view, for the pull-out
        self.us_k = view_of("US", pc)[2]

    def dots(self, img: np.ndarray, view, drive_s: float) -> np.ndarray:
        """Every store as a dot in its chain's colour, dimmed once the roads take over."""
        a0, a1, lo = DOT_DIM
        a = 1.0 if drive_s <= a0 else max(lo, 1 - (1 - lo) * (drive_s - a0) / (a1 - a0))
        x, y, k = view
        px = (self.d["sx"] - x) / k + W / 2
        py = (y - self.d["sy"]) / k + K.MAP_CENTER_Y
        m = (px > -5) & (px < W + 5) & (py > -5) & (py < H + 5)
        r = float(np.clip(1.2 + 0.9 * math.log2(max(self.us_k / k, 1.0)), 1.2, 4.5))
        layer = np.zeros_like(img)
        for c in (0, 1):
            col = tuple(int(v * a) for v in COLOURS[c])
            for u, v in zip(px[m & (self.d["sc"] == c)], py[m & (self.d["sc"] == c)]):
                cv2.circle(layer, (int(u * 16), int(v * 16)), int(r * 16), col, -1, cv2.LINE_AA, 4)
        return np.maximum(img, layer)

    # roads
    def in_view(self, idx, view):
        x, y, k = view
        mx, my = self.d["mx"], self.d["my"]
        cy = y - (H / 2 - K.MAP_CENTER_Y) * k                # frame centre in km
        return idx[(np.abs(mx[idx] - x) < W * k * 0.52) & (np.abs(my[idx] - cy) < H * k * 0.52)]

    def roads(self, i: int, view, upto: int) -> np.ndarray:
        order, pc, tf = self.d["order"], self.d["pc"], self.d["t_fade"]
        if self.cv is None or view != self.view or upto < self.done:
            self.cv = canvas_for(view)
            self.cv.add(pc, self.in_view(order[:upto], view), tf)
        else:
            self.cv.add(pc, self.in_view(order[self.done:upto], view), tf)
        self.view, self.done = view, upto
        return self.cv.img[:, :, ::-1].copy()                 # BGR -> RGB

    # text
    def end_rows(self):
        dn, st = self.d["share"]
        n = self.d["stores"]
        return ([(f"{round(100 * st)}%", "of major roads: Starbucks is closer", TEXT[1]),
                 (f"{round(100 * dn)}%", "of major roads: Dunkin' is closer", TEXT[0]),
                 (f"{n['starbucks']:,} vs {n['dunkin']:,}", "Starbucks vs. Dunkin' stores", K.INK),
                 (hm(self.d["ts"][-1]), "longest drive to either: West Texas", K.INK)],
                ["Drive time from the nearest store, no traffic",
                 "Stores: AllThePlaces (Oct 2026)   Roads: OpenStreetMap   Routing: OSRM"])

    def label(self, name: str) -> tuple[str, str, str]:
        sp = self.d["split"]
        d, s = sp.get(name, (0, 0))
        kick, line, place = LABELS[name]
        f = dict(d=d, s=s, d_ny=sp["ny"][0], d_ph=sp["ph"][0])
        return kick, line.format(**f), place.format(**f)

    def hud(self, im: Image.Image, i: int, held: str | None) -> None:
        sec = self.secs[i]
        d = ImageDraw.Draw(im)
        card_a = K.smoothstep((sec - CARD_IN) / 0.8)
        K.header(d, "Dunkin' vs. Starbucks", "Every store leaves at once. First to each road wins.")
        up = int(self.upto[i]) if sec >= RACE_START else 0
        c0, c1 = self.d["cum"][0][up], self.d["cum"][1][up]
        if card_a < 1:
            for c, (big, unit, col) in enumerate([(hm(self.drive[i]), "drive time", K.INK),
                                                   (f"{round(100 * c0)}%", "roads to Dunkin'", TEXT[0]),
                                                   (f"{round(100 * c1)}%", "roads to Starbucks", TEXT[1])]):
                x = W * (c + 0.5) / 3
                d.text((x, K.TOP + 175), big, font=K.F("big"), fill=col, anchor="mt")
                d.text((x, K.TOP + 270), unit, font=K.F("unit"), fill=K.MUTED, anchor="mt")
            bar(d, c0, c1)
        K.watermark(d)
        if held in LABELS and RACE_START <= sec <= RACE_END:
            a, b = held_range(held)
            al = min(1.0, (sec - a) / 0.4, (b - sec) / 0.4)
            if al > 0:
                kick, line, place = self.label(held)
                layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
                K.now_label(ImageDraw.Draw(layer), kick, line, place)
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * al)))
                im.alpha_composite(layer)
        if up >= len(self.d["ts"]) and sec <= CARD_IN:          # the last road: a callout where it is
            j = self.d["last"]
            (vx, vy, vk), _ = camera(sec, self.d["pc"], self.card_view)
            x = (self.d["mx"][j] - vx) / vk + W / 2
            y = (vy - self.d["my"][j]) / vk + K.MAP_CENTER_Y
            al = min(1.0, (sec - self.secs[np.argmax(self.upto >= len(self.d["ts"]))]) / 0.2)
            over = Image.new("RGBA", im.size, (0, 0, 0, 0))
            K.callout(ImageDraw.Draw(over), x, y, f"Last road: {hm(self.d['ts'][-1])}", al)
            im.alpha_composite(over)
        hook_a = 1 - K.smoothstep((sec - (K.HOOK - 0.3)) / 0.5)
        dn, st = self.d["share"]
        if hook_a > 0:
            hook(im, [(f"Starbucks {round(100 * st)}%", TEXT[1]), (f"Dunkin' {round(100 * dn)}%", TEXT[0])],
                 "of US roads: whose coffee is the closer drive", hook_a)
        end_card(im, *self.end_rows(), card_a)

    def frame(self, i: int) -> bytes:
        sec = self.secs[i]
        view, held = camera(sec, self.d["pc"], self.card_view)
        if held == "hook":
            if self.hook_img is None:
                self.hook_img = self.roads(i, view, len(self.d["ts"]))
            img = self.hook_img
        elif sec > RACE_END:                                         # pull-out: resample the finished map
            if self.final is None:
                self.final = self.roads(i, view_of("US", self.d["pc"]), len(self.d["ts"]))
            (ux, uy, uk), (x, y, k) = view_of("US", self.d["pc"]), view
            sc = uk / k
            m = np.float32([[sc, 0, W / 2 - W / 2 * sc + (ux - x) / k],
                            [0, sc, K.MAP_CENTER_Y - K.MAP_CENTER_Y * sc + (y - uy) / k]])
            img = cv2.warpAffine(self.final, m, (W, H), flags=cv2.INTER_AREA)
        else:
            img = self.dots(self.roads(i, view, int(self.upto[i])), view, self.drive[i])
            fa = 1 - (sec - K.HOOK) / FADE_S                        # the finished map fades out
            if 0 < fa <= 1 and self.hook_img is not None:
                img = (img * (1 - fa) + self.hook_img * fa).astype(np.uint8)
        im = Image.fromarray(img).convert("RGBA")
        im.alpha_composite(self.shade)
        self.hud(im, i, held)
        return im.convert("RGB").tobytes()


def held_range(name: str) -> tuple[float, float]:
    for (s0, v0), (s1, v1) in zip(SHOTS, SHOTS[1:]):
        if v0 == v1 == name:
            return s0, s1
    return SHOTS[-1][0], RACE_END


def hm(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60}:{m % 60:02d}"


def bar(d: ImageDraw.ImageDraw, c0: float, c1: float) -> None:
    """Tug of war where the chips go: Dunkin' fills from the left, Starbucks from the right."""
    x0, x1, y0, y1 = 36, W - 36, K.TOP + 330, K.TOP + 390
    d.rounded_rectangle((x0, y0, x1, y1), 12, fill=K.CHIP_FILL, outline=K.BORDER, width=2)
    w = x1 - x0 - 4
    if c0 > 0:
        d.rounded_rectangle((x0 + 2, y0 + 2, x0 + 2 + max(24, w * c0), y1 - 2), 10, fill=COLOURS[0])
    if c1 > 0:
        d.rounded_rectangle((x1 - 2 - max(24, w * c1), y0 + 2, x1 - 2, y1 - 2), 10, fill=COLOURS[1])
    d.text((x0 + 16, (y0 + y1) / 2), "DUNKIN'", font=K.F("tag"), fill=K.INK, anchor="lm")
    d.text((x1 - 16, (y0 + y1) / 2), "STARBUCKS", font=K.F("tag"), fill=K.INK, anchor="rm")


def end_card(im: Image.Image, rows: list[tuple[str, str, tuple]], footer: list[str], alpha: float) -> int:
    """K.end_card with a colour per row (the chains first)."""
    top = K.TOP + 160
    bottom = top + 40 + len(rows) * 110 + 40 + 36 * (len(footer) + 1) + 30
    if alpha <= 0:
        return bottom
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((50, top, W - 50, bottom), 28, fill=K.CARD_FILL, outline=K.BORDER, width=2)
    for r, (big, label, col) in enumerate(rows):
        y = top + 40 + r * 110
        d.text((96, y), big, font=K.F("card"), fill=col)
        d.text((W - 96, y + 34), label, font=K.F("small"), fill=K.MUTED, anchor="rm")
    y = top + 40 + len(rows) * 110 + 30
    for line in footer:
        d.text((W / 2, y), line, font=K.F("small"), fill=K.MUTED, anchor="mt")
        y += 36
    d.text((W / 2, y + 4), f"{K.SERIES}  ·  {K.HANDLE}", font=K.F("label"), fill=K.INK, anchor="mt")
    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    im.alpha_composite(layer)
    return bottom


def hook(im: Image.Image, lines: list[tuple[str, tuple]], sub: str, alpha: float) -> None:
    """K.hook with one colour per line (the chains)."""
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * alpha)
    lh = 128
    y0 = K.TEXT_FLOOR - 230 - (len(lines) * lh + 70) / 2
    d.rectangle((0, y0 - 50, W, y0 + len(lines) * lh + 110), fill=(0, 0, 0, int(170 * alpha)))
    for r, (text, col) in enumerate(lines):
        d.text((W / 2, y0 + r * lh), text, font=K.F("hook"), fill=(*col, a), anchor="mt")
    d.text((W / 2, y0 + len(lines) * lh + 20), sub, font=K.F("hook_sub"), fill=(*K.MUTED, a), anchor="mt")
    im.alpha_composite(layer)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["tiktok", "reels"], default="tiktok")
    ap.add_argument("--stills", help="comma-separated seconds; writes PNGs only")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    reel = Reel(args.platform)
    print(f"ready in {time.time() - t0:.0f} s; splits {reel.d['split']}; share {reel.d['share']}", flush=True)
    if args.stills:
        for sec in map(float, args.stills.split(",")):
            reel.cv = None
            i = min(int(sec * FPS), SECONDS * FPS - 1)
            Image.frombytes("RGB", (W, H), reel.frame(i)).save(OUT / f"reel_{args.platform}_{sec:04.1f}.png")
        print("stills written", flush=True)
        return
    out = OUT / f"turf_wars_{args.platform}.mp4"
    ff = K.encoder(out)
    n, t1 = SECONDS * FPS, time.time()
    for i in range(n):
        try:
            ff.stdin.write(reel.frame(i))
        except OSError:
            sys.exit(f"ffmpeg exited with {ff.wait()} at frame {i}")
        if i % 60 == 0:
            print(f"{i}/{n} frames, {time.time() - t1:.0f} s", flush=True)
    ff.stdin.close()
    if ff.wait():
        sys.exit(ff.returncode)
    print(f"wrote {out} in {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
