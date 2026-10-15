"""Optimal Drives house style: colours, fonts, layout, timing, sound, encoder.

The written spec is docs/optimal-drives-format.md; keep the two in step.
A video imports this and only supplies its own data, map and camera:

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import reel_kit as K
"""

from __future__ import annotations

import math
import os
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SERIES, HANDLE = "Optimal Drives", "@mapzimus"

# --- canvas and safe zones ---------------------------------------------------
W, H, FPS = 1080, 1920, 30
HUD_H = 430                      # series tag, title, stats, chips
TOP = 30                         # first HUD line
HUD_BOTTOM = TOP + HUD_H
NOW_TOP, NOW_BOTTOM = 1520, 1630  # the "now" label band
TEXT_FLOOR = 1640                # no text below: platform captions
RIGHT_RAIL = 140                 # px on the right, lower half: platform buttons
MAP_TOP, MAP_BOTTOM = HUD_BOTTOM, NOW_TOP
MAP_CENTER_Y = (MAP_TOP + MAP_BOTTOM) / 2

# Per-platform safe zones (doc §3). "classic" is the first video's layout.
# tiktok: the For You / Following tabs cover the top ~150 px, caption and
#   sound bar the bottom ~440 px.
# reels: Instagram also shows reels in the home feed cropped to 4:5
#   (y 285 to 1635), so the HUD starts below 285; the caption covers the
#   bottom ~420 px in the Reels tab.
PLATFORMS = {
    "classic": dict(top=30, floor=1640, rail=140),
    "tiktok": dict(top=150, floor=1480, rail=140),
    "reels": dict(top=285, floor=1500, rail=120),
}


def use(platform: str) -> None:
    """Switch every layout constant to one platform's safe zones."""
    global TOP, HUD_BOTTOM, NOW_TOP, NOW_BOTTOM, TEXT_FLOOR, RIGHT_RAIL, MAP_TOP, MAP_BOTTOM, MAP_CENTER_Y
    p = PLATFORMS[platform]
    TOP, TEXT_FLOOR, RIGHT_RAIL = p["top"], p["floor"], p["rail"]
    HUD_BOTTOM = TOP + HUD_H
    NOW_TOP, NOW_BOTTOM = TEXT_FLOOR - 120, TEXT_FLOOR - 10
    MAP_TOP, MAP_BOTTOM = HUD_BOTTOM, NOW_TOP
    MAP_CENTER_Y = (MAP_TOP + MAP_BOTTOM) / 2

# --- colour tokens ---------------------------------------------------------------
WATER = (13, 17, 23)
LAND = (33, 38, 48)
BORDER = (58, 66, 80)
INK = (236, 239, 244)
MUTED = (150, 158, 172)
DOT = (96, 104, 120)
ACCENT = (255, 176, 59)
ACCENT_GLOW = (120, 78, 24)
ACCENT_FILL = (46, 36, 20)       # finished chip
CHIP_FILL = (24, 29, 37)
CARD_FILL = (16, 20, 27, 236)

# --- timing (seconds) ----------------------------------------------------------
HOOK = 2.2          # answer on screen over the whole map
DIVE = 1.8          # hook fades, camera dives to the start
PULL_OUT = 1.8      # back out to the whole route
END_CARD = 4.2      # card fully up for at least this long

FONTS = Path("C:/Windows/Fonts")
_FONT_CACHE: dict = {}


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (name, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(str(FONTS / name), size)
    return _FONT_CACHE[key]


def F(role: str) -> ImageFont.FreeTypeFont:
    return font(*{
        "tag": ("segoeuib.ttf", 22), "title": ("segoeuib.ttf", 54), "sub": ("segoeui.ttf", 28),
        "big": ("bahnschrift.ttf", 84), "unit": ("segoeui.ttf", 24), "label": ("segoeuib.ttf", 30),
        "small": ("segoeui.ttf", 24), "chip": ("bahnschrift.ttf", 30), "chip_s": ("bahnschrift.ttf", 25),
        "card": ("bahnschrift.ttf", 64), "hook": ("bahnschrift.ttf", 118),
        "hook_sub": ("segoeuib.ttf", 34), "callout": ("segoeuib.ttf", 34),
    }[role])


def smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def clock(hours: float) -> str:
    m = int(round(hours * 60))
    return f"{m // 60}:{m % 60:02d}"


def spaced(text: str, n: int = 1) -> str:
    return (" " * n).join(text)


# --- HUD pieces ---------------------------------------------------------------------

def shade() -> Image.Image:
    """Top and bottom darkening so HUD text reads over any map."""
    col = np.zeros((H, 1, 4), np.uint8)
    for y in range(H):
        if y < HUD_BOTTOM + 60:
            pad = TOP - 30           # solid above the HUD; classic layout unchanged
            a = 1.0 if y < pad else (1 - (y - pad) / (HUD_BOTTOM + 60 - pad)) ** 1.4
        elif y > NOW_TOP - 140:
            a = min(1.0, (y - (NOW_TOP - 140)) / 260) ** 1.4 * 0.85
        else:
            a = 0.0
        col[y, 0] = (*WATER, int(a * 235))
    return Image.fromarray(np.repeat(col, W, axis=1), "RGBA")


def header(d: ImageDraw.ImageDraw, title: str, subtitle: str) -> None:
    d.text((W / 2, TOP), spaced(SERIES.upper()), font=F("tag"), fill=ACCENT, anchor="mt")
    d.text((W / 2, TOP + 34), title, font=F("title"), fill=INK, anchor="mt")
    d.text((W / 2, TOP + 104), subtitle, font=F("sub"), fill=MUTED, anchor="mt")


def stats(d: ImageDraw.ImageDraw, items: list[tuple[str, str, bool]]) -> None:
    """Up to 3 (number, unit, accent). The first is the clock."""
    for c, (big, unit, accent) in enumerate(items):
        x = W * (c + 0.5) / len(items)
        d.text((x, TOP + 175), big, font=F("big"), fill=ACCENT if accent else INK, anchor="mt")
        d.text((x, TOP + 270), unit, font=F("unit"), fill=MUTED, anchor="mt")


def chips(d: ImageDraw.ImageDraw, items: list[tuple[str, int, int]],
          colors: list[tuple] | None = None) -> list[bool]:
    """Up to 6 (label, done, total). Returns which are complete.
    With `colors`, each chip's label is always in its colour (a legend), and so is a full chip."""
    n, gap = len(items), 12
    cw = (W - 72 - gap * (n - 1)) / n
    out = []
    for c, (label, done, total) in enumerate(items):
        x = 36 + c * (cw + gap)
        full = done >= total
        col = colors[c] if colors else ACCENT
        fill = tuple(int(v * 0.18) for v in col) if colors else ACCENT_FILL
        d.rounded_rectangle((x, TOP + 330, x + cw, TOP + 390), 12, fill=fill if full else CHIP_FILL,
                            outline=col if full else BORDER, width=2)
        d.text((x + 14, TOP + 360), label, font=F("tag"), fill=col if full or colors else MUTED, anchor="lm")
        d.text((x + cw - 12, TOP + 360), f"{done}/{total}", font=F("chip_s") if total >= 100 else F("chip"),
               fill=INK if done else MUTED, anchor="rm")
        out.append(full)
    return out


def watermark(d: ImageDraw.ImageDraw, alpha: float = 1.0) -> None:
    c = tuple(int(WATER[i] + (MUTED[i] - WATER[i]) * 0.8 * alpha) for i in range(3))
    d.text((36, MAP_TOP + 14), HANDLE, font=F("small"), fill=c)


def now_label(d: ImageDraw.ImageDraw, kicker: str, name: str, place: str) -> None:
    d.text((W / 2, NOW_TOP), kicker.upper(), font=F("tag"), fill=ACCENT, anchor="mt")
    d.text((W / 2, NOW_TOP + 34), name, font=F("label"), fill=INK, anchor="mt")
    d.text((W / 2, NOW_TOP + 78), place, font=F("small"), fill=MUTED, anchor="mt")


def callout_box(x: float, y: float, text: str) -> tuple[float, float, float, float]:
    """Where callout() puts its box: (left, top, right, bottom), clear of the HUD and right rail."""
    tw = F("callout").getlength(text)
    bx = min(max(x - tw / 2, 30), W - RIGHT_RAIL - tw - 10)
    by = y - 74 if y - 74 > MAP_TOP + 50 else y + 30
    return bx - 14, by - 8, bx + tw + 14, by + 46


def callout(d: ImageDraw.ImageDraw, x: float, y: float, text: str, alpha: float,
            color: tuple = ACCENT, box: tuple | None = None) -> None:
    """Famous-place label on the map. `box` (from callout_box) overrides the placement."""
    l, t, r, b = box or callout_box(x, y, text)
    a = int(255 * alpha)
    d.rounded_rectangle((l, t, r, b), 10, fill=(*WATER, int(a * 0.85)), outline=(*color, a), width=2)
    d.text((l + 14, t + 8), text, font=F("callout"), fill=(*INK, a))


def hook(im: Image.Image, big: list[str], sub: str, alpha: float, center_y: float | None = None) -> None:
    """Frame-1 answer: big lines over the map. Doubles as the thumbnail.
    `center_y` moves it off the subject (default: centre of the map area)."""
    if alpha <= 0:
        return
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * alpha)
    lh = 128
    y0 = (MAP_CENTER_Y if center_y is None else center_y) - (len(big) * lh + 70) / 2
    d.rectangle((0, y0 - 50, W, y0 + len(big) * lh + 110), fill=(*WATER, int(150 * alpha)))
    for i, line in enumerate(big):
        d.text((W / 2, y0 + i * lh), line, font=F("hook"), fill=(*(ACCENT if i == 0 else INK), a), anchor="mt")
    d.text((W / 2, y0 + len(big) * lh + 20), sub, font=F("hook_sub"), fill=(*MUTED, a), anchor="mt")
    im.alpha_composite(layer)


def end_card(im: Image.Image, rows: list[tuple[str, str]], footer: list[str], alpha: float,
             top: int | None = None) -> int:
    """Up to 4 (number, label) rows, then footer lines and the sign-off.
    Returns the card's bottom edge so the caller can fit the map below."""
    top = TOP + 160 if top is None else top
    bottom = top + 40 + len(rows) * 110 + 40 + 36 * (len(footer) + 1) + 30
    if alpha <= 0:
        return bottom
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((50, top, W - 50, bottom), 28, fill=CARD_FILL, outline=BORDER, width=2)
    for r, (big, label) in enumerate(rows):
        y = top + 40 + r * 110
        d.text((96, y), big, font=F("card"), fill=ACCENT if r == 0 else INK)
        d.text((W - 96, y + 34), label, font=F("small"), fill=MUTED, anchor="rm")
    y = top + 40 + len(rows) * 110 + 30
    for line in footer:
        d.text((W / 2, y), line, font=F("small"), fill=MUTED, anchor="mt")
        y += 36
    d.text((W / 2, y + 4), f"{SERIES}  ·  {HANDLE}", font=F("label"), fill=ACCENT, anchor="mt")
    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    im.alpha_composite(layer)
    return bottom


# --- ferries and flights (doc §8) -----------------------------------------------------
# Shapes from experiments/isochrone (animate.py, ferries_anim.py), nose along +x.

_HALF = [(13, 0), (10, 1.4), (3, 1.7), (-2, 8.5), (-5, 8.5), (-3, 1.7),
         (-8, 1.5), (-10.5, 4.5), (-12.5, 4.5), (-11.5, 0)]
PLANE = _HALF + [(x, -y) for x, y in reversed(_HALF[1:-1])]
BOAT = [(13, 0), (5, 5), (-9, 5), (-11, 0), (-9, -5), (5, -5)]


def glyph(d: ImageDraw.ImageDraw, shape: list, x: float, y: float, angle: float, scale: float) -> None:
    """Plane or boat at (x, y), pointing along `angle` (radians, screen space)."""
    c, s = math.cos(angle), math.sin(angle)
    d.polygon([(x + scale * (px * c - py * s), y + scale * (px * s + py * c)) for px, py in shape],
              fill=INK, outline=WATER)


def arc(p0, p1, lift: float = 0.22):
    """Quadratic curve between screen points, bowed up like a flight path.
    Returns at(f) -> (x, y, heading)."""
    (ax, ay), (bx, by) = p0, p1
    cx, cy = (ax + bx) / 2, (ay + by) / 2 - lift * math.hypot(bx - ax, by - ay)

    def at(f: float):
        x = (1 - f) ** 2 * ax + 2 * (1 - f) * f * cx + f * f * bx
        y = (1 - f) ** 2 * ay + 2 * (1 - f) * f * cy + f * f * by
        dx = 2 * (1 - f) * (cx - ax) + 2 * f * (bx - cx)
        dy = 2 * (1 - f) * (cy - ay) + 2 * f * (by - cy)
        return x, y, math.atan2(dy, dx)
    return at


def dashed(d: ImageDraw.ImageDraw, pts: list, fill, width: int, dash: float, gap: float) -> None:
    """Dashed polyline (ferry crossings)."""
    on, left = True, dash
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        pos = 0.0
        while seg - pos > 1e-6:
            step = min(left, seg - pos)
            if on:
                f0, f1 = pos / seg, (pos + step) / seg
                d.line([(x0 + (x1 - x0) * f0, y0 + (y1 - y0) * f0),
                        (x0 + (x1 - x0) * f1, y0 + (y1 - y0) * f1)], fill=fill, width=width)
            pos += step
            left -= step
            if left <= 1e-6:
                on = not on
                left = dash if on else gap


# --- pacing ---------------------------------------------------------------------------

def warp(stop_t: np.ndarray, total: float, n_frames: int, clock_share: float = 0.5) -> np.ndarray:
    """Drive time for each frame of the drive section. Progress is a blend of
    the clock and the stop count, so clusters slow down and empty roads speed
    up. Returns monotonic hours from 0 to total."""
    grid = np.linspace(0, total, 20000)
    by_stop = np.interp(grid, stop_t, np.arange(len(stop_t))) / max(1, len(stop_t) - 1)
    progress = clock_share * grid / total + (1 - clock_share) * by_stop
    return np.interp(np.linspace(0, 1, n_frames), progress, grid)


# --- sound ------------------------------------------------------------------------------

def soundtrack(path: Path, seconds: float, ticks: list[float], chimes: list[float],
               finish: float | None, rate: int = 48000) -> None:
    """Ticks per stop, chimes per finished group, a chord at the finish."""
    n = int(seconds * rate)
    out = np.zeros(n)

    def add(t0: float, freqs, dur: float, amp: float, decay: float) -> None:
        i = int(t0 * rate)
        m = min(int(dur * rate), n - i)
        if m <= 0:
            return
        tt = np.arange(m) / rate
        env = np.exp(-tt / decay) * np.minimum(1, tt / 0.003)
        out[i:i + m] += amp * env * sum(np.sin(2 * np.pi * f * tt) for f in freqs) / len(freqs)

    for k, t in enumerate(ticks):
        add(t, [1760 * 2 ** ((k % 5) / 24)], 0.12, 0.22, 0.025)
    for t in chimes:
        add(t, [1318.5, 1975.5, 2637], 1.2, 0.30, 0.35)
    if finish is not None:
        add(finish, [220, 277.2, 329.6, 440], 3.5, 0.45, 1.2)
    peak = np.abs(out).max() or 1.0
    pcm = (out / peak * 0.8 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


# --- encoder ------------------------------------------------------------------------------

# GPU by default (RTX 4070, 2026-10-14): NVENC frees the CPU threads libx264 took, for
# drawing frames. OD_ENCODER=x264 falls back to the CPU (slow preset, CRF 18).
CODEC = {
    "nvenc": ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "19",
              "-b:v", "0", "-spatial_aq", "1", "-temporal_aq", "1"],
    "x264": ["-c:v", "libx264", "-preset", "slow", "-crf", "18"],
}


def encoder(path: Path) -> subprocess.Popen:
    codec = CODEC[os.environ.get("OD_ENCODER", "nvenc")]
    return subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", *codec,
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
        stdin=subprocess.PIPE)


def mux(video: Path, audio: Path, out: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(audio),
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", str(out)], check=True)


