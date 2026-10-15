"""Idea #40: the drive that crosses the same state border the most times, 65 s reel.

The winner of search.py (cache/results.json): one real fastest route that keeps
switching between two states. The route draws on in the colour of the state the
car is in (dataviz categorical slots 1 and 2, dark steps, validated adjacent);
every crossing gets a ding that climbs a half-step each time, a pulse on the
counter, and a numbered marker. Camera follows the car, pulls out to the whole
route, holds the finished map, then the end card ranks the runner-up borders.

    python experiments/border_crossings/video.py --prep          # engine up: route + steps + places -> cache/route.json
    python experiments/border_crossings/video.py --stills 1,8,30,55,63
    python experiments/border_crossings/video.py --platform tiktok
    python experiments/border_crossings/video.py --platform reels

Writes out/border_<platform>.mp4 with the dings mixed in.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import cv2
import numpy as np
import shapefile
import shapely
from PIL import Image, ImageDraw
from shapely.geometry import shape

HERE = Path(__file__).resolve().parent
CACHE, OUT = HERE / "cache", HERE / "out"
sys.path[:0] = [str(HERE.parent), str(HERE)]
import reel_kit as K  # noqa: E402
from search import OSRM, crossings, decode, states as load_states  # noqa: E402

sys.path.insert(0, str(HERE.parent / "dunkin-vs-starbucks"))
from draw import albers  # noqa: E402

FPS, W, H = K.FPS, K.W, K.H
SECONDS = 65
DRIVE_START = K.HOOK + K.DIVE
CARD_IN = SECONDS - K.END_CARD - 0.8
FINAL_HOLD = 6.0                       # whole route, finished (Max: "show the final map longer")
PULL = CARD_IN - FINAL_HOLD - K.PULL_OUT
ARRIVE = PULL - 1.0                    # the car reaches the end here, a beat before the pull-out
FOLLOW_KM = 34.0                       # follow-cam frame width
CLUSTER_KM = 4.0                       # ...zoomed in where crossings bunch up
CLUSTER_GAP_KM = 2.5                   # crossings closer than this along the route are a cluster
# State colours: dataviz categorical slots 1 and 2, dark-mode steps (blue #3987e5, orange #d95926).
SIDE = [(0x39, 0x87, 0xE5), (0xD9, 0x59, 0x26)]
FILL_A = 0.16                          # state wash opacity
BORDER_RGB = (236, 239, 244)
ROAD_RGB = (52, 58, 70)
OTHER_STATE = (22, 26, 33)
NAMES = {"AL": "Alabama", "AR": "Arkansas", "AZ": "Arizona", "CA": "California", "CO": "Colorado",
         "CT": "Connecticut", "DC": "D.C.", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "IA": "Iowa",
         "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
         "MA": "Massachusetts", "MD": "Maryland", "ME": "Maine", "MI": "Michigan", "MN": "Minnesota",
         "MO": "Missouri", "MS": "Mississippi", "MT": "Montana", "NC": "North Carolina", "ND": "North Dakota",
         "NE": "Nebraska", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NV": "Nevada",
         "NY": "New York", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
         "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
         "UT": "Utah", "VA": "Virginia", "VT": "Vermont", "WA": "Washington", "WI": "Wisconsin",
         "WV": "West Virginia", "WY": "Wyoming"}


# --- data -------------------------------------------------------------------------------

def winners() -> list[dict]:
    res = json.load(open(CACHE / "results.json"))
    return sorted((v[0] for v in res.values() if v), key=lambda r: (-r["count"], r["s"]))


def place(lon: float, lat: float) -> str:
    q = urllib.parse.urlencode(dict(lat=lat, lon=lon, format="json", zoom=12, addressdetails=1))
    req = urllib.request.Request(f"https://nominatim.openstreetmap.org/reverse?{q}",
                                 headers={"User-Agent": "my-osrm border video (mapzimus)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        a = json.load(r).get("address", {})
    time.sleep(1.1)
    town = a.get("city") or a.get("town") or a.get("village") or a.get("hamlet") or a.get("county", "")
    return town


def prep() -> None:
    """Route geometry, step names and place names for the winner (needs the engine)."""
    best = winners()[0]
    (lo0, la0), (lo1, la1) = best["start"], best["end"]
    r = json.load(urllib.request.urlopen(
        f"{OSRM}/route/v1/driving/{lo0:.6f},{la0:.6f};{lo1:.6f},{la1:.6f}"
        "?overview=full&geometries=polyline6&steps=true", timeout=120))["routes"][0]
    steps = [dict(name=s["ref"] if s.get("ref") else s["name"], dist=s["distance"], dur=s["duration"])
             for s in r["legs"][0]["steps"]]
    out = dict(best=best, geometry=r["geometry"], distance=r["distance"], duration=r["duration"], steps=steps,
               start_name=place(lo0, la0), end_name=place(lo1, la1), runners=winners()[:6])
    json.dump(out, open(CACHE / "route.json", "w"), indent=1)
    print(f"{best['border']}: {best['count']} crossings, {r['distance'] / 1609:.1f} mi, {r['duration'] / 60:.0f} min, "
          f"{out['start_name']} -> {out['end_name']}")


def load() -> dict:
    rt = json.load(open(CACHE / "route.json"))
    a, b = rt["best"]["border"].split("-")
    st = load_states()
    ll = decode(rt["geometry"])
    xy = np.column_stack(albers(ll[:, 0], ll[:, 1]))
    line = shapely.line_merge(shapely.intersection(st[a].boundary, st[b].boundary))
    box = shapely.box(*shapely.MultiPoint(xy).buffer(80).bounds)
    pa, pb = shapely.intersection(st[a], box), shapely.intersection(st[b], box)
    for g in (pa, pb, line):
        shapely.prepare(g)
    n, at = crossings(xy, pa, pb, line)
    seg = np.hypot(*np.diff(xy, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])                       # km along the route
    side = shapely.contains_xy(pb, xy[:, 0], xy[:, 1]).astype(int)      # 0 = in a, 1 = in b
    # each vertex's side, cleaned: between crossings the side is fixed (no flicker near the line)
    first = int(side[0])
    clean = np.full(len(xy), first)
    for k, i in enumerate(at):
        clean[i:] = (first + k + 1) % 2
    # time along the route: scale distance to the engine's duration per step
    t = cum / cum[-1] * rt["duration"]
    names, d0 = [], 0.0
    for s in rt["steps"]:
        names.append((d0 / 1000, s["name"]))
        d0 += s["dist"]
    return dict(rt=rt, a=a, b=b, xy=xy, cum=cum, t=t, side=clean, cross=at, n=n, st=st, line=line,
                names=names, box=box)


# --- basemaps ------------------------------------------------------------------------------

class Base:
    """One static map image at a fixed km/px; views are cut from it with an affine warp."""

    def __init__(self, d: dict, bounds, kpp: float, road_w: int):
        x0, y0, x1, y1 = bounds
        self.kpp, self.x0, self.y1 = kpp, x0, y1
        w, h = int((x1 - x0) / kpp), int((y1 - y0) / kpp)
        img = np.zeros((h, w, 3), np.uint8)
        img[:] = K.WATER

        def px(c):
            return np.round(np.column_stack([(c[:, 0] - x0) / kpp, (y1 - c[:, 1]) / kpp]) * 16).astype(np.int32)

        def fill(g, rgb):
            for p in getattr(g, "geoms", [g]):
                if p.geom_type != "Polygon" or p.is_empty:
                    continue
                rings = [px(np.asarray(p.exterior.coords))] + [px(np.asarray(r.coords)) for r in p.interiors]
                cv2.fillPoly(img, rings, rgb[::-1], cv2.LINE_AA, 4)

        view = shapely.box(*bounds)
        for k, g in d["st"].items():
            if k not in (d["a"], d["b"]) and g.intersects(view):
                fill(shapely.intersection(g, view), OTHER_STATE)
        for k, c in ((d["a"], SIDE[0]), (d["b"], SIDE[1])):
            mix = tuple(int(K.WATER[i] * (1 - FILL_A) + c[i] * FILL_A) for i in range(3))
            fill(shapely.intersection(d["st"][k], view), mix)
        r = np.load(HERE.parent / "dunkin-vs-starbucks" / "cache" / "roads.npz")
        lon, lat, start = r["lon"], r["lat"], r["start"]
        rx, ry = albers(lon, lat)
        inside = (rx > x0) & (rx < x1) & (ry > y0) & (ry < y1)
        for a, b in zip(start[:-1], start[1:]):
            if inside[a:b].any() and b - a > 1:
                cv2.polylines(img, [px(np.column_stack([rx[a:b], ry[a:b]]))], False, ROAD_RGB[::-1], road_w,
                              cv2.LINE_AA, 4)
        for p in getattr(d["line"], "geoms", [d["line"]]):
            cv2.polylines(img, [px(np.asarray(p.coords))], False, BORDER_RGB[::-1], max(2, road_w + 1), cv2.LINE_AA, 4)
        self.img = img[:, :, ::-1].copy()                                 # RGB

    def view(self, v) -> np.ndarray:
        x, y, k = v                                                       # centre km, km per output px
        s = self.kpp / k
        m = np.float32([[s, 0, W / 2 - (x - self.x0) / k], [0, s, K.MAP_CENTER_Y - (self.y1 - y) / k]])
        flags = cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR
        return cv2.warpAffine(self.img, m, (W, H), flags=flags, borderValue=K.WATER)


# --- camera & timing -------------------------------------------------------------------

def smooth(a, b, f: float):
    f = max(0.0, min(1.0, f))
    f = f * f * (3 - 2 * f)
    la, lb = math.log(a[2]), math.log(b[2])
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, math.exp(la + (lb - la) * f))


def hm(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def clock(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60}:{m % 60:02d}"


class Video:
    def __init__(self, platform: str):
        K.use(platform)
        self.d = d = load()
        xy = d["xy"]
        x0, y0 = xy.min(0)
        x1, y1 = xy.max(0)
        self.route_view = ((x0 + x1) / 2, (y0 + y1) / 2,
                           max((x1 - x0) * 1.25 / W, (y1 - y0) * 1.25 / (K.MAP_BOTTOM - K.MAP_TOP)))
        both = shapely.union(d["st"][d["a"]], d["st"][d["b"]]).bounds
        bx0, by0, bx1, by1 = both
        self.wide_view = ((bx0 + bx1) / 2, (by0 + by1) / 2,
                          max((bx1 - bx0) * 1.1 / W, (by1 - by0) * 1.1 / (K.MAP_BOTTOM - K.MAP_TOP)))
        pad = FOLLOW_KM * 1.2
        self.close = Base(d, (x0 - pad, y0 - pad * 1.9, x1 + pad, y1 + pad * 1.9), FOLLOW_KM / W / 1.5, 2)
        self.wide = Base(d, (bx0 - 150, by0 - 400, bx1 + 150, by1 + 400), self.wide_view[2] / 2, 1)
        # pacing: clock blended with crossings, so the dings don't bunch up
        tc = d["t"][d["cross"]] if d["cross"] else np.array([])
        stops = np.concatenate([[0.0], tc, [d["t"][-1]]])
        n = int((ARRIVE - DRIVE_START) * FPS)
        self.drive_t = K.warp(stops / 3600, d["t"][-1] / 3600, n, clock_share=0.45) * 3600
        self.cross_sec = [DRIVE_START + np.searchsorted(self.drive_t, d["t"][i]) / FPS for i in d["cross"]]
        self.zoom_plan()
        self.card_bottom = self.end_card(Image.new("RGBA", (1, 1)), 0)
        self.card_view = (self.route_view[0], self.route_view[1] + (K.MAP_CENTER_Y - (self.card_bottom + 30
                          + K.TEXT_FLOOR) / 2) * self.route_view[2] * 1.15, self.route_view[2] * 1.15
                          * (K.MAP_BOTTOM - K.MAP_TOP) / max(200, K.TEXT_FLOOR - self.card_bottom - 30))
        self.shade = K.shade()

    def drive_at(self, sec: float) -> float:
        if sec < DRIVE_START:
            return 0.0
        i = int((sec - DRIVE_START) * FPS)
        return float(self.drive_t[min(i, len(self.drive_t) - 1)])

    def car(self, ts: float) -> tuple[float, float, int]:
        """Car position (km) and vertex index at route time ts."""
        t, xy = self.d["t"], self.d["xy"]
        i = int(np.clip(np.searchsorted(t, ts, side="right") - 1, 0, len(t) - 2))
        f = 0.0 if t[i + 1] == t[i] else (ts - t[i]) / (t[i + 1] - t[i])
        p = xy[i] + (xy[i + 1] - xy[i]) * min(1.0, max(0.0, f))
        return float(p[0]), float(p[1]), i

    def zoom_plan(self) -> None:
        """Frame width (km) for every drive frame: FOLLOW_KM, but CLUSTER_KM while the car is near
        crossings that bunch up (twin towns, a road weaving along the line), eased over ~1.5 s."""
        d = self.d
        km_at = np.interp(self.drive_t, d["t"], d["cum"])
        cx = d["cum"][d["cross"]] if d["cross"] else np.array([])
        tight = np.zeros(len(km_at), bool)
        for k, c in enumerate(cx):
            close = np.abs(cx - c) < CLUSTER_GAP_KM
            if close.sum() > 1:
                tight |= np.abs(km_at - c) < CLUSTER_GAP_KM
        target = np.where(tight, np.log(CLUSTER_KM), np.log(FOLLOW_KM))
        win = int(1.5 * FPS)
        pad = np.concatenate([np.full(win, target[0]), target, np.full(win, target[-1])])
        smooth_ = np.convolve(pad, np.hanning(2 * win + 1) / np.hanning(2 * win + 1).sum(), mode="same")[win:-win]
        self.width = np.exp(smooth_)

    def follow(self, ts: float, sec: float | None = None):
        # look a little ahead and average, so the camera glides instead of jittering on curves
        width = FOLLOW_KM
        if sec is not None and sec >= DRIVE_START:
            width = float(self.width[min(int((sec - DRIVE_START) * FPS), len(self.width) - 1)])
        ahead = 180 * width / FOLLOW_KM
        pts = [self.car(min(self.d["t"][-1], ts + dt))[:2] for dt in (0, ahead / 3, 2 * ahead / 3, ahead)]
        x, y = np.mean(pts, axis=0)
        return float(x), float(y), width / W

    def camera(self, sec: float):
        if sec < K.HOOK:
            return self.route_view, "hook"
        start = self.follow(0.0)
        if sec < DRIVE_START:
            f = (sec - K.HOOK) / K.DIVE
            return (smooth(self.route_view, start, f), None)
        if sec <= PULL:
            return self.follow(self.drive_at(min(sec, ARRIVE)), min(sec, ARRIVE)), "drive"
        if sec <= PULL + K.PULL_OUT:
            return smooth(self.follow(self.d["t"][-1], ARRIVE), self.route_view, (sec - PULL) / K.PULL_OUT), None
        if sec <= CARD_IN:
            return self.route_view, "final"
        return smooth(self.route_view, self.card_view, (sec - CARD_IN) / 0.8), None

    # --- drawing ---
    def to_px(self, v, xy: np.ndarray) -> np.ndarray:
        x, y, k = v
        return np.column_stack([(xy[:, 0] - x) / k + W / 2, (y - xy[:, 1]) / k + K.MAP_CENTER_Y])

    def route(self, img: np.ndarray, v, ts: float) -> None:
        d = self.d
        cx, cy, i = self.car(ts)
        pts = np.vstack([d["xy"][:i + 1], [[cx, cy]]])
        side = np.concatenate([d["side"][:i + 1], [d["side"][i]]])
        p = np.round(self.to_px(v, pts) * 16).astype(np.int32)
        w = 9 if v[2] < 0.1 else 6
        cv2.polylines(img, [p], False, (0, 0, 0), w + 4, cv2.LINE_AA, 4)          # surface ring
        cuts = np.flatnonzero(np.diff(side)) + 1
        for a, b in zip(np.concatenate([[0], cuts]), np.concatenate([cuts + 1, [len(p)]])):
            if b - a > 1:
                cv2.polylines(img, [p[a:b]], False, SIDE[side[a]], w, cv2.LINE_AA, 4)

    def markers(self, im: Image.Image, v, sec: float, upto: int) -> None:
        """A numbered ring at each crossing passed so far; the newest one pulses."""
        d = ImageDraw.Draw(im)
        pts = self.to_px(v, self.d["xy"][self.d["cross"][:upto]])
        r = 15 if v[2] < 0.1 else 11
        for k, (x, y) in enumerate(pts):
            age = sec - self.cross_sec[k]
            grow = 1 + 0.8 * (1 - age / 0.35) if 0 <= age < 0.35 else 1.0   # pulse just after the crossing
            rr = r * grow
            d.ellipse((x - rr - 3, y - rr - 3, x + rr + 3, y + rr + 3), fill=(0, 0, 0))
            d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=BORDER_RGB)
            d.text((x, y + 1), str(k + 1), font=K.F("chip_s" if r < 15 else "chip"), fill=(0, 0, 0), anchor="mm")

    def head(self, img: np.ndarray, v, ts: float) -> None:
        cx, cy, i = self.car(ts)
        x, y = self.to_px(v, np.array([[cx, cy]]))[0]
        cv2.circle(img, (int(x * 16), int(y * 16)), 13 * 16, (0, 0, 0), -1, cv2.LINE_AA, 4)
        cv2.circle(img, (int(x * 16), int(y * 16)), 9 * 16, BORDER_RGB, -1, cv2.LINE_AA, 4)

    def road_name(self, ts: float) -> str:
        km = float(np.interp(ts, self.d["t"], self.d["cum"]))
        name = ""
        for d0, n in self.d["names"]:
            if d0 <= km and n:
                name = n
        return name

    def hud(self, im: Image.Image, sec: float, held: str | None, ts: float, upto: int) -> None:
        d = ImageDraw.Draw(im)
        dd = self.d
        a, b = NAMES[dd["a"]], NAMES[dd["b"]]
        card_a = K.smoothstep((sec - CARD_IN) / 0.8)
        K.header(d, f"{a} or {b}?", "One drive, the fastest way, back and forth across the line")
        if card_a < 1:
            pulse = max([0.0] + [1 - (sec - c) / 0.4 for c in self.cross_sec if 0 <= sec - c < 0.4])
            K.stats(d, [(clock(ts), "drive time", True), (str(upto), "border crossings", False),
                        (f"{np.interp(ts, dd['t'], dd['cum']) / 1.609:.0f} mi", "driven", False)])
            if pulse > 0:                                             # the counter flashes on a crossing
                x = W / 2
                d.rounded_rectangle((x - 120, K.TOP + 160, x + 120, K.TOP + 300), 18,
                                    outline=tuple(int(c * pulse) for c in BORDER_RGB), width=3)
            self.legend(d, ts)
        K.watermark(d, 1 - card_a)
        if held == "drive" and sec <= ARRIVE + 0.6:
            here = dd["side"][self.car(ts)[2]]
            road = self.road_name(ts)
            K.now_label(d, "Now in", NAMES[(dd["a"], dd["b"])[here]], f"on {road}" if road else "")
        if held == "final":
            al = min(1.0, (sec - (PULL + K.PULL_OUT)) / 0.4, (CARD_IN - sec) / 0.4)
            if al > 0:
                lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
                K.now_label(ImageDraw.Draw(lay), f"{dd['n']} crossings, one drive",
                            f"{dd['rt']['start_name']} to {dd['rt']['end_name']}",
                            f"{hm(dd['rt']['duration'])}, the fastest way")
                lay.putalpha(lay.getchannel("A").point(lambda v: int(v * al)))
                im.alpha_composite(lay)
        hook_a = 1 - K.smoothstep((sec - (K.HOOK - 0.3)) / 0.5)
        if hook_a > 0:
            K.hook(im, [f"{dd['n']} times", "across one line"],
                   f"The fastest drive from {dd['rt']['start_name']} to {dd['rt']['end_name']}", hook_a)
        self.end_card(im, card_a)

    def legend(self, d: ImageDraw.ImageDraw, ts: float) -> None:
        """Two state chips (the legend): swatch + name, the one you're in outlined."""
        dd = self.d
        here = dd["side"][self.car(ts)[2]]
        cw = (W - 72 - 12) / 2
        for c, code in enumerate((dd["a"], dd["b"])):
            x = 36 + c * (cw + 12)
            on = c == here
            d.rounded_rectangle((x, K.TOP + 330, x + cw, K.TOP + 390), 12, fill=K.CHIP_FILL,
                                outline=SIDE[c] if on else K.BORDER, width=3 if on else 2)
            d.rounded_rectangle((x + 16, K.TOP + 351, x + 34, K.TOP + 369), 4, fill=SIDE[c])
            d.text((x + 46, K.TOP + 360), NAMES[code].upper(), font=K.F("tag"),
                   fill=K.INK if on else K.MUTED, anchor="lm")

    def end_card(self, im: Image.Image, alpha: float) -> int:
        """KPI rows, then the top borders by crossings (ranked bars, one hue, value at the tip)."""
        dd = self.d
        rt = dd["rt"]
        rows = [(str(dd["n"]), f"times across the {NAMES[dd['a']]}–{NAMES[dd['b']]} line"),
                (hm(rt["duration"]), f"{rt['start_name']} to {rt['end_name']}"),
                (f"{rt['distance'] / 1609:.0f} mi", "the fastest route, no detours")]
        top = K.TOP + 160
        bars_top = top + 40 + len(rows) * 100 + 20
        runners = rt["runners"][:5]
        footer = ["Fastest route by OSRM, no traffic. A crossing counts once",
                  "the road is 100 m into the other state.   Borders: Census TIGER 2024"]
        bottom = bars_top + 50 + len(runners) * 44 + 30 + 36 * (len(footer) + 1) + 30
        if alpha <= 0:
            return bottom
        layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((50, top, W - 50, bottom), 28, fill=K.CARD_FILL, outline=K.BORDER, width=2)
        for r, (big, label) in enumerate(rows):
            y = top + 40 + r * 100
            d.text((96, y), big, font=K.F("card"), fill=K.INK)
            d.text((W - 96, y + 34), label, font=K.F("small"), fill=K.MUTED, anchor="rm")
        d.text((96, bars_top), "Most crossings, by border", font=K.F("label"), fill=K.INK)
        bx0, bx1 = 230, W - 150
        scale = (bx1 - bx0) / max(r["count"] for r in runners)
        for r, row in enumerate(runners):
            y = bars_top + 50 + r * 44 + 10
            a, b = row["border"].split("-")
            d.text((bx0 - 20, y + 12), f"{a}–{b}", font=K.F("label"), fill=K.MUTED, anchor="rm")
            d.rounded_rectangle((bx0, y, bx0 + row["count"] * scale, y + 24), 4, fill=SIDE[0],
                                corners=(False, True, True, False))
            d.text((bx0 + row["count"] * scale + 14, y + 12), str(row["count"]), font=K.F("label"),
                   fill=K.INK, anchor="lm")
        y = bars_top + 50 + len(runners) * 44 + 30
        for line in footer:
            d.text((W / 2, y), line, font=K.F("small"), fill=K.MUTED, anchor="mt")
            y += 36
        d.text((W / 2, y + 4), f"{K.SERIES}  ·  {K.HANDLE}", font=K.F("label"), fill=K.ACCENT, anchor="mt")
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
        im.alpha_composite(layer)
        return bottom

    def frame(self, i: int) -> bytes:
        sec = i / FPS
        v, held = self.camera(sec)
        ts = self.d["t"][-1] if held in ("hook", "final") or sec > ARRIVE else self.drive_at(sec)
        base = self.close if v[2] < self.close.kpp * 2.5 else self.wide
        img = base.view(v)
        self.route(img, v, ts)
        upto = sum(1 for c in self.cross_sec if c <= sec) if held != "hook" else self.d["n"]
        if held in ("hook", "final") or sec > ARRIVE:
            upto = self.d["n"]
        if held not in ("hook", "final") and sec <= ARRIVE + 0.5:
            self.head(img, v, ts)
        im = Image.fromarray(img).convert("RGBA")
        self.markers(im, v, sec, upto)
        im.alpha_composite(self.shade)
        self.hud(im, sec, held, ts, upto)
        return im.convert("RGB").tobytes()

    # --- sound ---
    def sound(self, path: Path, rate: int = 48000) -> None:
        """A ding per crossing, a half-step higher each time; a chord on arrival."""
        n = int(SECONDS * rate)
        out = np.zeros(n)

        def add(t0, freqs, dur, amp, decay):
            i = int(t0 * rate)
            m = min(int(dur * rate), n - i)
            if m <= 0:
                return
            tt = np.arange(m) / rate
            env = np.exp(-tt / decay) * np.minimum(1, tt / 0.003)
            out[i:i + m] += amp * env * sum(np.sin(2 * np.pi * f * tt) for f in freqs) / len(freqs)

        for k, t in enumerate(self.cross_sec):
            f0 = 880 * 2 ** (k / 12)
            add(t, [f0, f0 * 2, f0 * 3], 1.0, 0.35, 0.28)               # bell: fundamental + overtones
        add(ARRIVE, [261.6, 329.6, 392.0, 523.3], 3.5, 0.5, 1.2)
        pcm = (out / (np.abs(out).max() or 1) * 0.8 * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prep", action="store_true")
    ap.add_argument("--platform", choices=["tiktok", "reels"], default="tiktok")
    ap.add_argument("--stills")
    args = ap.parse_args()
    if args.prep:
        prep()
        return
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    v = Video(args.platform)
    print(f"ready in {time.time() - t0:.0f} s; {v.d['n']} crossings at {[round(c, 1) for c in v.cross_sec]}",
          flush=True)
    if args.stills:
        for sec in map(float, args.stills.split(",")):
            Image.frombytes("RGB", (W, H), v.frame(int(sec * FPS))).save(OUT / f"still_{args.platform}_{sec:04.1f}.png")
        print("stills written")
        return
    silent = OUT / f"border_{args.platform}_silent.mp4"
    ff = K.encoder(silent)
    n = SECONDS * FPS
    for i in range(n):
        ff.stdin.write(v.frame(i))
        if i % 150 == 0:
            print(f"{i}/{n} frames, {time.time() - t0:.0f} s", flush=True)
    ff.stdin.close()
    if ff.wait():
        sys.exit(ff.returncode)
    wav = OUT / "dings.wav"
    v.sound(wav)
    K.mux(silent, wav, OUT / f"border_{args.platform}.mp4")
    print(f"wrote {OUT / f'border_{args.platform}.mp4'} in {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
