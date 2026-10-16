"""Turf Wars: Wawa vs. Sheetz (Max, 2026-10-16). Every store of both chains leaves at once; each road
lights up in the colour of the closer one by drive time. First frame: both logos and the question;
the answer (each state's split by people) is the end card (docs/optimal-drives-format.md 6a).
A chime the first time the two meet in each state.

Built on experiments/nfl/video.py (projection, road canvas, camera, timing, render in contiguous
worker chunks). Data: cache/stores.csv + cache/region.json (stores.py), cache/drivetime.npz
(dunkin-vs-starbucks/drivetime.py --stores experiments/wawa-vs-sheetz/cache: the whole lower 48),
people: Census 2020 tracts in the region's states (hospitals/cache/CenPop2020_Mean_TR.txt).
--region draws only roads in the states where they compete (cache/road_mask.npy) to compare looks.

    python experiments/wawa-vs-sheetz/video.py --stills 1,8,20,40,63 [--region]
    pwsh experiments/render.ps1 wawa-vs-sheetz -Sound
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "nfl"))
import reel_kit as K  # noqa: E402
import video as N     # noqa: E402  (experiments/nfl/video.py)

ROOT = HERE.parents[1]
CACHE, OUT, ASSETS = HERE / "cache", HERE / "out", HERE / "assets"
ROADS = HERE.parent / "dunkin-vs-starbucks" / "cache" / "roads.npz"
TRACTS = HERE.parent / "hospitals" / "cache" / "CenPop2020_Mean_TR.txt"
FPS, W, H = K.FPS, K.W, K.H
SECONDS, RACE_START, CARD_IN, RACE_END = N.SECONDS, N.RACE_START, N.CARD_IN, N.RACE_END
REGION_ONLY = os.environ.get("WS_REGION") == "1"         # set by --region (inherited by workers)

CHAIN = {"wawa": ("Wawa", (0xF2, 0xB0, 0x1E)), "sheetz": ("Sheetz", (0xDA, 0x1A, 0x32))}   # both brands are red:
# Sheetz keeps its red, Wawa takes its gold (Max: team colours unless two similar ones collide)
FIPS = {"42": "PA", "34": "NJ", "10": "DE", "24": "MD", "11": "DC", "51": "VA", "54": "WV", "39": "OH",
        "37": "NC", "45": "SC", "13": "GA", "12": "FL", "01": "AL", "47": "TN", "21": "KY", "18": "IN", "26": "MI"}
NAME = {"PA": "Pennsylvania", "NJ": "New Jersey", "DE": "Delaware", "MD": "Maryland", "DC": "D.C.", "VA": "Virginia",
        "WV": "West Virginia", "OH": "Ohio", "NC": "North Carolina", "SC": "South Carolina", "GA": "Georgia",
        "FL": "Florida", "AL": "Alabama", "TN": "Tennessee", "KY": "Kentucky", "IN": "Indiana", "MI": "Michigan"}
CARD_STATES = ["NJ", "DE", "PA", "MD", "VA", "WV", "OH", "NC", "FL"]       # the end card's rows

VIEWS = {"Philly": (-75.25, 39.95, 220), "Altoona": (-78.4, 40.5, 220), "Pennsylvania": (-77.6, 40.9, 620),
         "DC": (-77.2, 38.9, 420), "Carolinas": (-79.6, 35.6, 800), "Ohio": (-82.6, 40.3, 650),
         "Florida": (-82.0, 28.3, 850), "East": (-80.0, 36.5, 1900), "US": None}
SHOTS = [(RACE_START, "Philly"), (6.4, "Philly"), (8.0, "Altoona"), (10.4, "Altoona"), (12.2, "Pennsylvania"),
         (16.0, "Pennsylvania"), (17.8, "DC"), (20.6, "DC"), (22.4, "Ohio"), (25.2, "Ohio"), (27.0, "Carolinas"),
         (29.8, "Carolinas"), (31.6, "Florida"), (34.2, "Florida"), (36.4, "East"), (40.6, "East")]
LABELS = {"Philly": ("Philadelphia", "NJ", "Wawa's first store: Folsom, PA, 1964"),
          "Altoona": ("Altoona", "PA", "Sheetz's first store: Altoona, 1952"),
          "Pennsylvania": ("Pennsylvania", "PA", "of Pennsylvanians, by the closer store"),
          "DC": ("Maryland", "MD", "of Marylanders, by the closer store"),
          "Ohio": ("Ohio", "OH", "of Ohioans, by the closer store"),
          "Carolinas": ("North Carolina", "NC", "of North Carolinians, by the closer store"),
          "Florida": ("Florida", "FL", "330 Wawas, no Sheetz"),
          "East": ("The East", None, "where each is the shorter drive")}


def prepare() -> dict:
    d = np.load(CACHE / "drivetime.npz")
    names = list(d["chains"])
    assert names == list(CHAIN)
    r = np.load(ROADS)
    lon, lat, start = r["lon"], r["lat"], r["start"]
    win, arr = d["winner"], d["arrival_s"]
    mask = np.load(CACHE / "road_mask.npy")
    x, y = N.albers(lon, lat)
    n = len(lon)
    last = np.zeros(n, bool)
    last[start[1:] - 1] = True
    a = np.flatnonzero(~last[:-1])
    b = a + 1
    mx, my = (x[a] + x[b]) / 2, (y[a] + y[b]) / 2
    p0 = np.column_stack([np.concatenate([x[a], mx]), np.concatenate([y[a], my])])
    p1 = np.column_stack([np.concatenate([mx, x[b]]), np.concatenate([my, y[b]])])
    st = np.concatenate([win[a], win[b]])
    t = np.concatenate([arr[a], arr[b]])
    inreg = np.concatenate([mask[a], mask[b]])
    ok = (st < 255) & np.isfinite(t) & (inreg if REGION_ONLY else True)
    p0, p1 = p0[ok].astype(np.float32), p1[ok].astype(np.float32)
    st, t, inreg = st[ok].astype(np.int16), t[ok].astype(np.float32), inreg[ok]
    mid = (p0 + p1) / 2
    bx = mid[inreg] if REGION_ONLY else mid
    bounds = (float(bx[:, 0].min()), float(bx[:, 1].min()), float(bx[:, 0].max()), float(bx[:, 1].max()))

    # people: tracts in the region's states take the chain of their nearest timed road point
    good = (win < 255) & np.isfinite(arr) & mask
    gi = np.flatnonzero(good)
    tree = cKDTree(np.column_stack([x[gi], y[gi]]))
    tl, tpop, tst = [], [], []
    with open(TRACTS, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if row["STATEFP"] in FIPS:
                tl.append((float(row["LONGITUDE"]), float(row["LATITUDE"])))
                tpop.append(int(row["POPULATION"]))
                tst.append(FIPS[row["STATEFP"]])
    tl = np.array(tl)
    tx, ty = N.albers(tl[:, 0], tl[:, 1])
    _, nn = tree.query(np.column_stack([tx, ty]))
    tw, tt = win[gi[nn]].astype(int), arr[gi[nn]]
    tpop, tst = np.array(tpop, float), np.array(tst)
    people = np.bincount(tw, weights=tpop, minlength=2)
    states = {}
    for s in set(tst):
        m = tst == s
        states[s] = np.bincount(tw[m], weights=tpop[m], minlength=2) / tpop[m].sum()
    o = np.argsort(tt)
    reach = dict(t=tt[o], cum=[np.concatenate([[0.0], np.cumsum(np.where(tw[o] == c, tpop[o], 0.0))]) for c in (0, 1)],
                 total=float(tpop.sum()))

    # first meeting in each state (chimes): road segments whose ends differ, state of the nearest tract
    bm = (win[a] != win[b]) & (win[a] < 255) & (win[b] < 255) & mask[a] & mask[b]
    tt_tree = cKDTree(np.column_stack([tx, ty]))
    _, bn = tt_tree.query(np.column_stack([mx[bm], my[bm]]))
    meet_t = np.maximum(arr[a][bm], arr[b][bm])
    meets = {}
    for s_, t_ in zip(tst[bn], meet_t):
        if np.isfinite(t_) and t_ < meets.get(s_, math.inf):
            meets[s_] = float(t_)

    stores = collections.defaultdict(list)
    for row in csv.DictReader(open(CACHE / "stores.csv", encoding="utf-8")):
        stores[row["chain"]].append((float(row["lon"]), float(row["lat"])))
    sxy = {c: np.column_stack(N.albers([p[0] for p in v], [p[1] for p in v])) for c, v in stores.items()}
    counts = {c: len(v) for c, v in stores.items()}
    jl = int(np.argmax(np.where(inreg, t, -1))) if REGION_ONLY else int(np.argmax(t))
    lx, ly = mid[jl]
    db = sqlite3.connect(ROOT / "data" / "app" / "places.sqlite")
    towns = db.execute("select name, lon, lat from places where kind in ('town','village','city') and country='US'").fetchall()
    tx2, ty2 = N.albers([q[1] for q in towns], [q[2] for q in towns])
    near = towns[int(np.argmin((tx2 - lx) ** 2 + (ty2 - ly) ** 2))][0]
    return dict(p0=p0, p1=p1, team=st, t=t, mid=mid, bounds=bounds, people=people, states=states, reach=reach,
                meets=meets, sxy=sxy, counts=counts, last=jl, last_town=near)


def view_of(name, b):
    if VIEWS[name] is None:
        return N.us_view(K.MAP_TOP, K.MAP_BOTTOM, b)
    lon, lat, width = VIEWS[name]
    x, y = N.albers([lon], [lat])
    return float(x[0]), float(y[0]), width / W


def camera(sec, b, card_view):
    if sec < K.HOOK:
        return view_of("US", b), "hook"
    if sec < RACE_START:
        return N.move(view_of("US", b), view_of(SHOTS[0][1], b), (sec - K.HOOK) / K.DIVE), None
    if sec > RACE_END:
        return N.move(view_of(SHOTS[-1][1], b), card_view, min(1.0, (sec - RACE_END) / K.PULL_OUT)), None
    for (s0, v0), (s1, v1) in zip(SHOTS, SHOTS[1:]):
        if sec <= s1:
            if v0 == v1:
                return view_of(v0, b), v0
            return N.move(view_of(v0, b), view_of(v1, b), (sec - s0) / (s1 - s0)), None
    return view_of(SHOTS[-1][1], b), SHOTS[-1][1]


def held_range(name):
    for (s0, v0), (s1, v1) in zip(SHOTS, SHOTS[1:]):
        if v0 == v1 == name:
            return s0, s1
    return SHOTS[-1][0], RACE_END


def pct(v: float) -> str:
    return f"{round(100 * v)}%"


class Reel:
    def __init__(self, platform):
        K.use(platform)
        D = self.D = prepare()
        self.order = np.argsort(D["t"], kind="stable")
        self.ts = D["t"][self.order]
        self.t_fade = float(np.percentile(self.ts, 99.5))
        self.bgr = [CHAIN[c][1][::-1] for c in CHAIN]
        n = SECONDS * FPS
        self.secs = np.arange(n) / FPS
        r0, r1 = int(RACE_START * FPS), int(RACE_END * FPS)
        drive = np.zeros(n)
        drive[r0:r1 + 1] = N.schedule(self.ts, r1 + 1 - r0)
        drive[r1 + 1:] = self.ts[-1]
        self.drive = drive
        self.upto = np.searchsorted(self.ts, drive, side="right")
        self.upto[:r0] = 0
        self.card_bottom = self.end_card(Image.new("RGBA", (1, 1)), 0)
        self.card_view = N.us_view(self.card_bottom + 30, K.TEXT_FLOOR, D["bounds"])
        self.shade = K.shade()
        self.cv, self.view, self.done = None, None, 0
        self.hook_img = self.final = None
        self.us_k = view_of("US", D["bounds"])[2]
        self.logo = {}
        for c, h in (("wawa", 150), ("sheetz", 110)):
            lg = Image.open(ASSETS / f"{c}.png").convert("RGBA")
            lg = lg.crop(lg.getbbox())
            self.logo[c] = lg.resize((round(lg.width * h / lg.height), h), Image.LANCZOS)
        self.chimes = []
        for t_ in sorted(D["meets"].values()):
            s = RACE_START + int(np.searchsorted(self.drive[r0:], t_)) / FPS
            if s < RACE_END and (not self.chimes or s - self.chimes[-1] >= N.CHIME_GAP):
                self.chimes.append(s)

    def roads(self, view, upto):
        D = self.D
        x, y, k = view
        cy = y - (H / 2 - K.MAP_CENTER_Y) * k
        thick, floor = N.close_up(view)

        def in_view(idx):
            m = D["mid"][idx]
            return idx[(np.abs(m[:, 0] - x) < W * k * 0.52) & (np.abs(m[:, 1] - cy) < H * k * 0.52)]
        if self.cv is None or view != self.view or upto < self.done:
            self.cv = N.Canvas(view)
            self.cv.add(D, in_view(self.order[:upto]), self.t_fade, self.bgr, thick, floor)
        else:
            self.cv.add(D, in_view(self.order[self.done:upto]), self.t_fade, self.bgr, thick, floor)
        self.view, self.done = view, upto
        return self.cv.img[:, :, ::-1].copy()

    def dots(self, img, view, drive_s):
        """Every store, a dot in its chain's colour; bright at the start, dim once roads take over."""
        x, y, k = view
        a = 1.0 if drive_s <= 120 else max(0.35, 1 - 0.65 * (drive_s - 120) / 1200)
        r = float(np.clip(1.3 + 0.9 * math.log2(max(self.us_k / k, 1.0)), 1.3, 4.5))
        layer = np.zeros_like(img)
        for ci, c in enumerate(CHAIN):
            pts = self.D["sxy"][c]
            u = (pts[:, 0] - x) / k + W / 2
            v = (y - pts[:, 1]) / k + K.MAP_CENTER_Y
            m = (u > -5) & (u < W + 5) & (v > -5) & (v < H + 5)
            col = tuple(int(q * a) for q in CHAIN[c][1])
            for uu, vv in zip(u[m], v[m]):
                cv2.circle(layer, (int(uu * 16), int(vv * 16)), int(r * 16), col, -1, cv2.LINE_AA, 4)
        return np.maximum(img, layer)

    def reached(self, drive_s):
        R = self.D["reach"]
        i = int(np.searchsorted(R["t"], drive_s, side="right"))
        return R["cum"][0][i] / R["total"], R["cum"][1][i] / R["total"]

    # --- text
    def end_card(self, im, alpha):
        """The answer (format doc 6a): the people split, then each state as a 100% bar (Wawa gold
        from the left, Sheetz red from the right), name and both shares in ink."""
        D = self.D
        top = K.TOP + 150
        row_h = 56
        bottom = top + 40 + 230 + len(CARD_STATES) * row_h + 30 + 36 * 2 + 50
        if alpha <= 0:
            return bottom
        layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((40, top, W - 40, bottom), 28, fill=K.CARD_FILL, outline=K.BORDER, width=2)
        y = top + 30
        d.text((W / 2, y), "WHO'S CLOSER, BY PEOPLE", font=K.F("tag"), fill=K.MUTED, anchor="mt")
        y += 44
        wl, sl = self.logo["wawa"], self.logo["sheetz"]
        sw = wl.resize((round(wl.width * 84 / wl.height), 84), Image.LANCZOS)
        ss = sl.resize((round(sl.width * 62 / sl.height), 62), Image.LANCZOS)
        pw, ps = D["people"] / D["people"].sum()
        layer.alpha_composite(sw, (int(W / 4 - sw.width / 2), int(y)))
        layer.alpha_composite(ss, (int(3 * W / 4 - ss.width / 2), int(y + 11)))
        y += 100
        big = K.F("hook_sub").font_variant(size=80)
        d.text((W / 4, y), pct(pw), font=big, fill=K.INK, anchor="mt")
        d.text((3 * W / 4, y), pct(ps), font=big, fill=K.INK, anchor="mt")
        y = top + 40 + 230
        x0, x1 = 300, W - 80
        for k_, s in enumerate(CARD_STATES):
            yy = y + k_ * row_h
            sh = D["states"][s]
            d.text((x0 - 16, yy + 26), NAME[s], font=K.F("small"), fill=K.INK, anchor="rm")
            xm = x0 + (x1 - x0) * sh[0]
            if sh[0] > 0.004:
                d.rounded_rectangle((x0, yy + 9, max(x0 + 8, xm - 1), yy + 43), 4, fill=CHAIN["wawa"][1])
            if sh[1] > 0.004:
                d.rounded_rectangle((min(x1 - 8, xm + 1), yy + 9, x1, yy + 43), 4, fill=CHAIN["sheetz"][1])
            if sh[0] >= 0.12:
                d.text((x0 + 10, yy + 26), pct(sh[0]), font=K.F("small"), fill=(20, 20, 24), anchor="lm")
            if sh[1] >= 0.12:
                d.text((x1 - 10, yy + 26), pct(sh[1]), font=K.F("small"), fill=K.INK, anchor="rm")
        y += len(CARD_STATES) * row_h + 30
        for line in [f"Stores: AllThePlaces + OpenStreetMap (Oct 2026): {D['counts']['wawa']:,} Wawa, "
                     f"{D['counts']['sheetz']:,} Sheetz",
                     "People: Census 2020 tracts, each to the shorter drive, no traffic"]:
            d.text((W / 2, y), line, font=K.F("small"), fill=K.MUTED, anchor="mt")
            y += 36
        d.text((W / 2, y + 4), f"{K.SERIES}  ·  {K.HANDLE}", font=K.F("label"), fill=K.INK, anchor="mt")
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
        im.alpha_composite(layer)
        return bottom

    def label(self, held):
        kick, s, place = LABELS[held]
        D = self.D
        if s is None:
            pw, ps = D["people"] / D["people"].sum()
            return kick, f"Wawa {pct(pw)} · Sheetz {pct(ps)}", place
        sh = D["states"][s]
        return kick, f"Wawa {pct(sh[0])} · Sheetz {pct(sh[1])}", place if held not in ("Philly", "Altoona") else place

    def bar(self, d, c0, c1):
        x0, x1, y0, y1 = 36, W - 36, K.TOP + 330, K.TOP + 390
        d.rounded_rectangle((x0, y0, x1, y1), 12, fill=K.CHIP_FILL, outline=K.BORDER, width=2)
        w = x1 - x0 - 4
        if c0 > 0:
            d.rounded_rectangle((x0 + 2, y0 + 2, x0 + 2 + max(24, w * c0), y1 - 2), 10, fill=CHAIN["wawa"][1])
        if c1 > 0:
            d.rounded_rectangle((x1 - 2 - max(24, w * c1), y0 + 2, x1 - 2, y1 - 2), 10, fill=CHAIN["sheetz"][1])
        d.text((x0 + 16, (y0 + y1) / 2), "WAWA", font=K.F("tag"), fill=(20, 20, 24), anchor="lm")
        d.text((x1 - 16, (y0 + y1) / 2), "SHEETZ", font=K.F("tag"), fill=K.INK, anchor="rm")

    def hook(self, im, alpha):
        """First frame: both logos and the question (format doc 6a)."""
        if alpha <= 0:
            return
        layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        dd = ImageDraw.Draw(layer)
        a = int(255 * alpha)
        lines = ["Where does", "Wawa country end?"]
        f0 = K.F("hook")
        size = f0.size
        while max(f0.font_variant(size=size).getlength(t_) for t_ in lines) > W - 90:
            size -= 4
        f = f0.font_variant(size=size)
        lh = int(size * 1.12)
        y0 = K.TEXT_FLOOR - 200 - len(lines) * lh / 2
        dd.rectangle((0, y0 - 40, W, y0 + len(lines) * lh + 40), fill=(0, 0, 0, int(170 * alpha)))
        for r_, t_ in enumerate(lines):
            dd.text((W / 2, y0 + r_ * lh), t_, font=f, fill=(*(K.INK if r_ == 0 else CHAIN["wawa"][1]), a), anchor="mt")
        wl, sl = self.logo["wawa"], self.logo["sheetz"]
        gap = 50
        tw = wl.width + sl.width + gap
        lx = (W - tw) / 2
        ly = y0 - 70 - wl.height
        for lg, xx, yy in ((wl, lx, ly), (sl, lx + wl.width + gap, ly + (wl.height - sl.height) / 2)):
            g = lg
            if alpha < 1:
                g = lg.copy()
                g.putalpha(g.getchannel("A").point(lambda v: int(v * alpha)))
            layer.alpha_composite(g, (int(xx), int(yy)))
        im.alpha_composite(layer)

    def hud(self, im, i, held):
        sec = self.secs[i]
        d = ImageDraw.Draw(im)
        card_a = K.smoothstep((sec - CARD_IN) / 0.8)
        K.header(d, "Wawa vs. Sheetz", "Each road is colored by the closer store")
        if card_a < 1:
            c0, c1 = self.reached(self.drive[i]) if sec >= RACE_START else (0.0, 0.0)
            for c, (big, unit, col) in enumerate([(N.hm(self.drive[i]), "drive time", K.INK),
                                                   (pct(c0), "of people: Wawa", CHAIN["wawa"][1]),
                                                   (pct(c1), "of people: Sheetz", CHAIN["sheetz"][1])]):
                xx = W * (c + 0.5) / 3
                d.text((xx, K.TOP + 175), big, font=K.F("big"), fill=K.INK if c == 0 else col, anchor="mt")
                d.text((xx, K.TOP + 270), unit, font=K.F("unit"), fill=K.MUTED, anchor="mt")
            self.bar(d, c0, c1)
        K.watermark(d, 1 - card_a)
        if held in LABELS and RACE_START <= sec <= RACE_END:
            a, b = held_range(held)
            al = min(1.0, (sec - a) / 0.4, (b - sec) / 0.4)
            if al > 0:
                layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
                K.now_label(ImageDraw.Draw(layer), *self.label(held))
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * al)))
                im.alpha_composite(layer)
        if int(self.upto[i]) >= len(self.ts) and sec <= CARD_IN:
            j = self.D["last"]
            (vx, vy, vk), _ = camera(sec, self.D["bounds"], self.card_view)
            xx = (self.D["mid"][j, 0] - vx) / vk + W / 2
            yy = (vy - self.D["mid"][j, 1]) / vk + K.MAP_CENTER_Y
            al = min(1.0, (sec - self.secs[np.argmax(self.upto >= len(self.ts))]) / 0.2)
            over = Image.new("RGBA", im.size, (0, 0, 0, 0))
            K.callout(ImageDraw.Draw(over), xx, yy, f"Last road: {N.hm(self.ts[-1])}, near {self.D['last_town']}", al)
            im.alpha_composite(over)
        self.hook(im, 1 - K.smoothstep((sec - (K.HOOK - 0.3)) / 0.5))
        self.end_card(im, card_a)

    def frame(self, i):
        sec = self.secs[i]
        D = self.D
        view, held = camera(sec, D["bounds"], self.card_view)
        if held == "hook":
            if self.hook_img is None:
                self.hook_img = self.roads(view, len(self.ts))
            img = self.hook_img.copy()
        elif sec > RACE_END and view[2] > 0.5 * self.us_k:
            if self.final is None:
                ux, uy, uk = view_of("US", D["bounds"])
                big = N.Canvas((ux, uy, uk / 2))
                big.img = np.zeros((2 * H, 2 * W, 3), np.uint8)
                big.ox, big.oy = W - ux * 2 / uk, 2 * K.MAP_CENTER_Y + uy * 2 / uk
                big.add(D, self.order, self.t_fade, self.bgr, 2)
                self.final = big.img[:, :, ::-1].copy()
            (ux, uy, uk), (x, y, k) = view_of("US", D["bounds"]), view
            sc = uk / (2 * k)
            m = np.float32([[sc, 0, W / 2 - uk / k * W / 2 + (ux - x) / k],
                            [0, sc, K.MAP_CENTER_Y - uk / k * K.MAP_CENTER_Y + (y - uy) / k]])
            img = cv2.warpAffine(self.final, m, (W, H), flags=cv2.INTER_AREA)
        else:
            img = self.roads(view, int(self.upto[i]))
            fa = 1 - (sec - K.HOOK) / N.FADE_S
            if 0 < fa <= 1 and self.hook_img is not None:
                img = (img * (1 - fa) + self.hook_img * fa).astype(np.uint8)
        img = self.dots(img, view, float(self.drive[i]) if held != "hook" else 0.0)
        im = Image.fromarray(img).convert("RGBA")
        im.alpha_composite(self.shade)
        self.hud(im, i, held)
        return im.convert("RGB").tobytes()


def render_range(args):
    platform, a, b, path = args
    reel = Reel(platform)
    ff = K.encoder(path)
    for i in range(a, b):
        ff.stdin.write(reel.frame(i))
    ff.stdin.close()
    return ff.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["tiktok", "reels"], default="tiktok")
    ap.add_argument("--stills", help="comma-separated seconds; writes PNGs only")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--part", help="k/n")
    ap.add_argument("--join", type=int)
    ap.add_argument("--sound", action="store_true", help="a chime at each state's first meeting, a chord")
    ap.add_argument("--region", action="store_true", help="only roads in the states where they compete")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.region:
        os.environ["WS_REGION"] = "1"
        global REGION_ONLY
        REGION_ONLY = True
    OUT.mkdir(exist_ok=True)
    slug = f"turf_wars_wawa_sheetz{'_region' if REGION_ONLY else ''}_{args.platform}"
    if args.report:
        reel = Reel(args.platform)
        D = reel.D
        pw, ps = D["people"] / D["people"].sum()
        print(f"people: Wawa {D['people'][0] / 1e6:.1f} M ({pct(pw)}), Sheetz {D['people'][1] / 1e6:.1f} M ({pct(ps)})")
        for s in sorted(D["states"], key=lambda s: -D["states"][s][0]):
            print(f"  {s}: Wawa {pct(D['states'][s][0])} Sheetz {pct(D['states'][s][1])}")
        print("first meetings (state, h):", sorted((round(v / 3600, 2), k) for k, v in D["meets"].items()))
        print(f"{len(reel.chimes)} chimes; last road {N.hm(reel.ts[-1])} near {D['last_town']}")
        return
    if args.stills:
        reel = Reel(args.platform)
        for sec in map(float, args.stills.split(",")):
            reel.cv = None
            i = min(int(sec * FPS), SECONDS * FPS - 1)
            Image.frombytes("RGB", (W, H), reel.frame(i)).save(
                OUT / f"{args.platform}{'_region' if REGION_ONLY else ''}_{sec:04.1f}.png")
        return
    n = SECONDS * FPS
    silent = OUT / f"{slug}_silent.mp4"
    if args.join:
        N.concat([OUT / f"{slug}_part{k}.mp4" for k in range(1, args.join + 1)], silent)
    else:
        a, b = 0, n
        if args.part:
            k, m = map(int, args.part.split("/"))
            a, b = (k - 1) * n // m, k * n // m
            silent = OUT / f"{slug}_part{k}.mp4"
        w = max(1, args.workers)
        cuts = [a + (b - a) * q // w for q in range(w + 1)]
        subs = [OUT / f"{silent.stem}_w{q}.mp4" for q in range(w)]
        t0 = time.time()
        with Pool(w) as pool:
            codes = pool.map(render_range, [(args.platform, cuts[q], cuts[q + 1], subs[q]) for q in range(w)])
        if any(codes):
            sys.exit(f"encoder failed: {codes}")
        N.concat(subs, silent)
        print(f"wrote {silent} ({b - a} frames) in {time.time() - t0:.0f} s", flush=True)
        if args.part:
            return
    out = OUT / f"{slug}.mp4"
    if args.sound:
        reel = Reel(args.platform)
        wav = OUT / f"{slug}.wav"
        K.soundtrack(wav, SECONDS, [], reel.chimes, RACE_END)
        K.mux(silent, wav, out)
        silent.unlink()
        wav.unlink()
    else:
        silent.replace(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
