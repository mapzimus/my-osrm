"""Turf Wars rough cut: roads coloured by whichever chain gets there first.

Black background; each road piece is orange where Dunkin' arrives first and
green where Starbucks does, brightest near a store, fading with arrival time.
A piece whose two ends have different winners is split at its middle.

    python experiments/dunkin-vs-starbucks/draw.py              # still: out/still.png (+ preview)
    python experiments/dunkin-vs-starbucks/draw.py --width 3840

Brightness depends only on arrival time, so the animation can add newly
reached pieces onto the previous frame (Canvas.add).
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
CACHE, OUT = HERE / "cache", HERE / "out"
# chain index (drivetime.winner) -> colour, RGB
COLOURS = [(0xFF, 0x6E, 0x1B), (0x00, 0x70, 0x4A)]          # Dunkin' orange, Starbucks green
LEVELS = 32                       # brightness steps (one cv2 call per colour per step)
FADE_FLOOR = 0.22                 # brightness of the slowest roads
FADE_POWER = 0.6
SHIFT = 4                         # cv2 sub-pixel bits
CHUNK = 200_000                   # pieces per cv2 call
MARGIN = 0.04

# --- Albers equal-area, lower 48 (USGS parameters), km ----------------------------
R, LON0, P1, P2, PHI0 = 6371.0, -96.0, 29.5, 45.5, 23.0
_n = (math.sin(math.radians(P1)) + math.sin(math.radians(P2))) / 2
_C = math.cos(math.radians(P1)) ** 2 + 2 * _n * math.sin(math.radians(P1))
_rho0 = R * math.sqrt(_C - 2 * _n * math.sin(math.radians(PHI0))) / _n


def albers(lon, lat):
    lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    rho = R * np.sqrt(_C - 2 * _n * np.sin(lat)) / _n
    th = _n * (lon - math.radians(LON0))
    return rho * np.sin(th), _rho0 - rho * np.cos(th)


def load():
    r, d = np.load(CACHE / "roads.npz"), np.load(CACHE / "drivetime.npz")
    return dict(lon=r["lon"], lat=r["lat"], start=r["start"], cls=r["cls"],
                winner=d["winner"], arrival=d["arrival_s"])


def pieces(data: dict) -> dict:
    """Half-segments: every road segment a-b becomes a-mid (a's winner and
    arrival) and mid-b (b's). Returns km endpoints, chain, arrival."""
    lon, lat, start = data["lon"], data["lat"], data["start"]
    n = len(lon)
    last = np.zeros(n, bool)
    last[start[1:] - 1] = True                     # final point of each road: no segment after it
    a = np.flatnonzero(~last[:-1])
    b = a + 1
    x, y = albers(lon, lat)
    mx, my = (x[a] + x[b]) / 2, (y[a] + y[b]) / 2
    win, arr = data["winner"], data["arrival"]
    p0x = np.concatenate([x[a], mx]); p0y = np.concatenate([y[a], my])
    p1x = np.concatenate([mx, x[b]]); p1y = np.concatenate([my, y[b]])
    ch = np.concatenate([win[a], win[b]])
    t = np.concatenate([arr[a], arr[b]])
    ok = (ch < 2) & np.isfinite(t)
    return dict(p0=np.column_stack([p0x, p0y])[ok].astype(np.float32),
                p1=np.column_stack([p1x, p1y])[ok].astype(np.float32),
                chain=ch[ok].astype(np.int8), t=t[ok].astype(np.float32),
                bounds=(x.min(), y.min(), x.max(), y.max()))


class Canvas:
    """A W x H black image of the lower 48 that pieces are added onto."""

    def __init__(self, bounds, w: int, h: int | None = None, t_scale: float = 1.0, box=None):
        x0, y0, x1, y1 = bounds
        self.w = w
        # box = (left, top, right, bottom) in px where the map goes; default: whole image with a margin
        if h is None:                                          # fit the height to the map
            h = int(round((1 - 2 * MARGIN) * w * (y1 - y0) / (x1 - x0) + 2 * MARGIN * w))
        self.h = h
        bl, bt, br, bb = box or (MARGIN * w, MARGIN * w, w - MARGIN * w, h - MARGIN * w)
        self.s = min((br - bl) / (x1 - x0), (bb - bt) / (y1 - y0))
        self.ox = (bl + br) / 2 - (x0 + x1) / 2 * self.s
        self.oy = (bt + bb) / 2 + (y0 + y1) / 2 * self.s
        self.img = np.zeros((h, w, 3), np.uint8)              # BGR for cv2
        self.t_scale = t_scale

    def px(self, p: np.ndarray) -> np.ndarray:
        return np.column_stack([p[:, 0] * self.s + self.ox, self.oy - p[:, 1] * self.s])

    def add(self, pc: dict, sel: np.ndarray, t_max: float, thick: int = 1) -> None:
        """Draw pieces[sel]; brightness falls from 1 at t=0 to FADE_FLOOR at t_max."""
        if not len(sel):
            return
        f = np.clip(pc["t"][sel] / t_max, 0, 1) ** FADE_POWER
        lvl = np.minimum((f * LEVELS).astype(np.int8), LEVELS - 1)
        ch = pc["chain"][sel]
        for L in range(LEVELS - 1, -1, -1):                   # dim first, bright on top
            k = 1 - (1 - FADE_FLOOR) * (L + 0.5) / LEVELS
            for c, rgb in enumerate(COLOURS):
                idx = sel[(lvl == L) & (ch == c)]
                for a in range(0, len(idx), CHUNK):           # bounded memory: cv2 wants one array per line
                    j = idx[a:a + CHUNK]
                    p = np.round(np.stack([self.px(pc["p0"][j]), self.px(pc["p1"][j])], 1)
                                 * (1 << SHIFT)).astype(np.int32).reshape(-1, 2, 1, 2)
                    cv2.polylines(self.img, list(p), False, tuple(int(v * k) for v in rgb[::-1]),
                                  thick, cv2.LINE_AA, SHIFT)

    def save(self, path: Path) -> None:
        cv2.imwrite(str(path), self.img)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=7680)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    pc = pieces(load())
    t_max = float(np.percentile(pc["t"], 99.5))
    cv = Canvas(pc["bounds"], args.width)
    cv.add(pc, np.arange(len(pc["t"])), t_max, thick=max(1, args.width // 3840))
    cv.save(OUT / "still.png")
    prev = cv2.resize(cv.img, (1920, int(1920 * cv.h / cv.w)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT / "still_preview.png"), prev)
    won = np.bincount(pc["chain"], minlength=2)
    print(f"{len(pc['t']):,} half-segments; Dunkin' {won[0] / won.sum():.1%}, Starbucks {won[1] / won.sum():.1%}; "
          f"fade to {FADE_FLOOR} at {t_max / 3600:.2f} h (99.5th pct); max {pc['t'].max() / 3600:.2f} h; "
          f"{cv.w}x{cv.h} -> {OUT / 'still.png'}")


if __name__ == "__main__":
    main()
