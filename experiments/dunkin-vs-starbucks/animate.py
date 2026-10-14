"""Turf Wars rough cut, animated: every store leaves at once and each road
lights up when the first chain reaches it (draw.py's colours and fading).

The spread runs SPREAD_S seconds, then the finished map holds for HOLD_S.
Arrival times are very skewed (half the roads in 5 minutes, the last ones
after 6 h), so video time follows a blend of the clock and the share of
roads reached, like reel_kit.warp; the clock still ends at the true maximum.

    python experiments/dunkin-vs-starbucks/animate.py --format 16x9
    python experiments/dunkin-vs-starbucks/animate.py --format 9x16
    python experiments/dunkin-vs-starbucks/animate.py --format 16x9 --stills 2,8,15,27

Writes out/turf_dunkin_starbucks_<format>.mp4 (or out/anim_<format>_<s>.png).

Camera (Max, 2026-10-14: "should zoom and stuff too"): SHOTS, down I-95 from
Dunkin's home turf to the most even 50/50 frontier, then out to the country.
While the camera holds, new roads are added onto one canvas; while it moves,
each frame is redrawn from the roads in view.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from draw import CACHE, OUT, Canvas, albers, load, pieces

FPS = 30
SPREAD_S, HOLD_S = 25.0, 3.0
CLOCK_SHARE = 0.35
SIZES = {"16x9": (1920, 1080), "9x16": (1080, 1920)}
FONT = "C:/Windows/Fonts/bahnschrift.ttf"
INK, LABEL = (225, 225, 225), (150, 150, 150)     # greys: same in RGB and BGR

# Views: (lon, lat, short side of the frame in km); "US" = the whole map as in the still.
VIEWS = {"Boston": (-71.06, 42.36, 110), "Northeast": (-73.6, 41.5, 650),
         "DC-Baltimore": (-76.9, 39.1, 220), "US": None}   # DC-Baltimore: 254k road points, 50/50
# (video seconds, view): hold each view until the next key, moving (smoothstep) where the key says.
SHOTS = [(0.0, "Boston"), (3.0, "Boston"), (5.5, "Northeast"), (7.0, "Northeast"), (9.5, "DC-Baltimore"),
         (14.0, "DC-Baltimore"), (18.0, "US")]


def schedule(t_sorted: np.ndarray, n: int) -> np.ndarray:
    """Drive time (s) for each of n spread frames: progress, a blend of the
    clock and the share of pieces reached, runs linearly from 0 to 1."""
    t_end = float(t_sorted[-1])
    grid = np.linspace(0.0, t_end, 40_000)
    share = np.searchsorted(t_sorted, grid, side="right") / len(t_sorted)
    progress = CLOCK_SHARE * grid / t_end + (1 - CLOCK_SHARE) * share
    return np.interp(np.linspace(0.0, 1.0, n), progress, grid)


class Clock:
    """Drive time as h:mm with a small label, top-left (16:9) or top-centre (9:16)."""

    def __init__(self, fmt: str, w: int, h: int):
        big = 56 if fmt == "16x9" else 72
        self.digits = ImageFont.truetype(FONT, big)
        self.small = ImageFont.truetype(FONT, int(big * 0.36))
        self.fmt, self.w = fmt, w
        self.top = 48 if fmt == "16x9" else 440

    def draw(self, img: np.ndarray, t_s: float) -> None:
        im = Image.fromarray(img)
        d = ImageDraw.Draw(im)
        m = int(t_s // 60)
        txt = f"{m // 60}:{m % 60:02d}"
        if self.fmt == "16x9":
            d.text((48, self.top), txt, font=self.digits, fill=INK, anchor="lt")
            d.text((50, self.top + self.digits.size + 10), "drive time", font=self.small, fill=LABEL, anchor="lt")
        else:
            d.text((self.w / 2, self.top), txt, font=self.digits, fill=INK, anchor="mt")
            d.text((self.w / 2, self.top + self.digits.size + 12), "drive time", font=self.small, fill=LABEL,
                   anchor="mt")
        img[:] = np.asarray(im)


def view_of(name: str, us: Canvas, w: int, h: int) -> tuple[float, float, float]:
    """(centre x km, centre y km, km per pixel)."""
    if VIEWS[name] is None:
        return (w / 2 - us.ox) / us.s, (us.oy - h / 2) / us.s, 1 / us.s
    lon, lat, short = VIEWS[name]
    x, y = albers([lon], [lat])
    return float(x[0]), float(y[0]), short / min(w, h)


def camera(sec: float, us: Canvas, w: int, h: int) -> tuple[tuple, bool]:
    """View at video second sec, and whether the camera is still."""
    if sec <= SHOTS[0][0]:
        return view_of(SHOTS[0][1], us, w, h), True
    for (s0, v0), (s1, v1) in zip(SHOTS, SHOTS[1:]):
        if sec <= s1:
            a, b = view_of(v0, us, w, h), view_of(v1, us, w, h)
            if v0 == v1:
                return a, True
            k = (sec - s0) / (s1 - s0)
            k = k * k * (3 - 2 * k)
            return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k,
                    float(np.exp(np.log(a[2]) + (np.log(b[2]) - np.log(a[2])) * k))), False
    return view_of(SHOTS[-1][1], us, w, h), True


def canvas_for(view, w: int, h: int) -> Canvas:
    x, y, k = view
    return Canvas((x - w * k / 2, y - h * k / 2, x + w * k / 2, y + h * k / 2), w, h, box=(0, 0, w, h))


class Frames:
    """Frame images in order: incremental while the view holds, redrawn while it moves."""

    def __init__(self, pc, order, upto, t_fade, w, h):
        self.pc, self.order, self.upto, self.t_fade, self.w, self.h = pc, order, upto, t_fade, w, h
        self.mx = (pc["p0"][:, 0] + pc["p1"][:, 0]) / 2
        self.my = (pc["p0"][:, 1] + pc["p1"][:, 1]) / 2
        self.us = Canvas(pc["bounds"], w, h)
        self.cv, self.view, self.done = None, None, 0

    def in_view(self, idx: np.ndarray, view) -> np.ndarray:
        x, y, k = view
        hw, hh = self.w * k * 0.52, self.h * k * 0.52
        return idx[(np.abs(self.mx[idx] - x) < hw) & (np.abs(self.my[idx] - y) < hh)]

    def get(self, i: int, sec: float) -> np.ndarray:
        view, still = camera(sec, self.us, self.w, self.h)
        if self.cv is None or view != self.view:                 # new view: draw what's reached so far
            self.cv = canvas_for(view, self.w, self.h)
            self.cv.add(self.pc, self.in_view(self.order[:self.upto[i]], view), self.t_fade)
        else:                                                    # same view: add the new roads
            self.cv.add(self.pc, self.in_view(self.order[self.done:self.upto[i]], view), self.t_fade)
        self.view, self.done = view, self.upto[i]
        return self.cv.img.copy()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=list(SIZES), default="16x9")
    ap.add_argument("--stills", help="comma-separated seconds; writes PNGs only")
    args = ap.parse_args()
    w, h = SIZES[args.format]
    OUT.mkdir(exist_ok=True)

    t0 = time.time()
    pc = pieces(load())
    order = np.argsort(pc["t"], kind="stable")
    ts = pc["t"][order]
    t_fade = float(np.percentile(ts, 99.5))
    n_spread, n_hold = int(SPREAD_S * FPS), int(HOLD_S * FPS)
    drive = np.concatenate([schedule(ts, n_spread), np.full(n_hold, float(ts[-1]))])
    upto = np.searchsorted(ts, drive, side="right")             # pieces shown by each frame
    frames = Frames(pc, order, upto, t_fade, w, h)
    clock = Clock(args.format, w, h)
    print(f"{len(ts):,} pieces loaded in {time.time() - t0:.0f} s; {len(drive)} frames; "
          f"clock ends at {drive[-1] / 3600:.2f} h", flush=True)

    if args.stills:
        for sec in sorted(map(float, args.stills.split(","))):
            i = min(int(sec * FPS), len(drive) - 1)
            frames.cv = None                                     # stills: draw each from scratch
            frame = frames.get(i, i / FPS)
            clock.draw(frame, drive[i])
            Image.fromarray(frame[:, :, ::-1]).save(OUT / f"anim_{args.format}_{sec:04.1f}.png")
        print("stills written", flush=True)
        return

    out = OUT / f"turf_dunkin_starbucks_{args.format}.mp4"
    with open(CACHE / f"ffmpeg_{args.format}.log", "w") as log:
        ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                               "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "18",
                               "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                              stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        t1 = time.time()
        for i in range(len(drive)):
            frame = frames.get(i, i / FPS)
            clock.draw(frame, drive[i])
            try:
                ff.stdin.write(frame.tobytes())
            except OSError:
                sys.exit(f"ffmpeg exited with {ff.wait()} at frame {i}")
            if i % 60 == 0:
                print(f"{i}/{len(drive)} frames, {time.time() - t1:.0f} s", flush=True)
        ff.stdin.close()
        if ff.wait():
            sys.exit(f"ffmpeg failed ({ff.returncode}), see {CACHE / f'ffmpeg_{args.format}.log'}")
    print(f"wrote {out} in {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
