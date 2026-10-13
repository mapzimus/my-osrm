"""Optimal Drives house style: colours, fonts, layout, timing, sound, encoder.

The written spec is docs/optimal-drives-format.md; keep the two in step.
A video imports this and only supplies its own data, map and camera:

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import reel_kit as K
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SERIES, HANDLE = "Optimal Drives", "@mapzimus"

# --- canvas and safe zones ---------------------------------------------------
W, H, FPS = 1080, 1920, 30
HUD_BOTTOM = 460                 # series tag, title, stats, chips live above
NOW_TOP, NOW_BOTTOM = 1520, 1630  # the "now" label band
TEXT_FLOOR = 1640                # no text below: platform captions
RIGHT_RAIL = 140                 # px on the right, lower half: platform buttons
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
        if y < 520:
            a = (1 - y / 520) ** 1.4
        elif y > NOW_TOP - 140:
            a = min(1.0, (y - (NOW_TOP - 140)) / 260) ** 1.4 * 0.85
        else:
            a = 0.0
        col[y, 0] = (*WATER, int(a * 235))
    return Image.fromarray(np.repeat(col, W, axis=1), "RGBA")


def header(d: ImageDraw.ImageDraw, title: str, subtitle: str) -> None:
    d.text((W / 2, 30), spaced(SERIES.upper()), font=F("tag"), fill=ACCENT, anchor="mt")
    d.text((W / 2, 64), title, font=F("title"), fill=INK, anchor="mt")
    d.text((W / 2, 134), subtitle, font=F("sub"), fill=MUTED, anchor="mt")


def stats(d: ImageDraw.ImageDraw, items: list[tuple[str, str, bool]]) -> None:
    """Up to 3 (number, unit, accent). The first is the clock."""
    for c, (big, unit, accent) in enumerate(items):
        x = W * (c + 0.5) / len(items)
        d.text((x, 205), big, font=F("big"), fill=ACCENT if accent else INK, anchor="mt")
        d.text((x, 300), unit, font=F("unit"), fill=MUTED, anchor="mt")


def chips(d: ImageDraw.ImageDraw, items: list[tuple[str, int, int]]) -> list[bool]:
    """Up to 6 (label, done, total). Returns which are complete."""
    n, gap = len(items), 12
    cw = (W - 72 - gap * (n - 1)) / n
    out = []
    for c, (label, done, total) in enumerate(items):
        x = 36 + c * (cw + gap)
        full = done >= total
        d.rounded_rectangle((x, 360, x + cw, 420), 12, fill=ACCENT_FILL if full else CHIP_FILL,
                            outline=ACCENT if full else BORDER, width=2)
        d.text((x + 14, 390), label, font=F("tag"), fill=ACCENT if full else MUTED, anchor="lm")
        d.text((x + cw - 12, 390), f"{done}/{total}", font=F("chip_s") if total >= 100 else F("chip"),
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


def callout(d: ImageDraw.ImageDraw, x: float, y: float, text: str, alpha: float) -> None:
    """Famous-place label on the map, kept clear of the HUD and right rail."""
    f = F("callout")
    tw = f.getlength(text)
    bx = min(max(x - tw / 2, 30), W - RIGHT_RAIL - tw - 10)
    by = y - 74 if y - 74 > MAP_TOP + 50 else y + 30
    a = int(255 * alpha)
    d.rounded_rectangle((bx - 14, by - 8, bx + tw + 14, by + 46), 10,
                        fill=(*WATER, int(a * 0.85)), outline=(*ACCENT, a), width=2)
    d.text((bx, by), text, font=f, fill=(*INK, a))


def hook(im: Image.Image, big: list[str], sub: str, alpha: float) -> None:
    """Frame-1 answer: big lines over the map. Doubles as the thumbnail."""
    if alpha <= 0:
        return
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * alpha)
    lh = 128
    y0 = MAP_CENTER_Y - (len(big) * lh + 70) / 2
    d.rectangle((0, y0 - 50, W, y0 + len(big) * lh + 110), fill=(*WATER, int(150 * alpha)))
    for i, line in enumerate(big):
        d.text((W / 2, y0 + i * lh), line, font=F("hook"), fill=(*(ACCENT if i == 0 else INK), a), anchor="mt")
    d.text((W / 2, y0 + len(big) * lh + 20), sub, font=F("hook_sub"), fill=(*MUTED, a), anchor="mt")
    im.alpha_composite(layer)


def end_card(im: Image.Image, rows: list[tuple[str, str]], footer: list[str], alpha: float,
             top: int = 190) -> int:
    """Up to 4 (number, label) rows, then footer lines and the sign-off.
    Returns the card's bottom edge so the caller can fit the map below."""
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

def encoder(path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow",
         "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
        stdin=subprocess.PIPE)


def mux(video: Path, audio: Path, out: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(audio),
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", str(out)], check=True)


