"""Turf Wars: every NFL stadium (Max, 2026-10-16). All 30 lower-48 stadiums leave at once; each
road lights up in the colour of the stadium that is the shortest drive away, the team's logo
growing inside its own region. A chime each time two regions meet for the first time. Ends
on the teams with the most people closest to their stadium.

Data: cache/stores.csv (30 stadiums, teams.py), cache/drivetime.npz (dunkin-vs-starbucks/
drivetime.py --stores experiments/nfl/cache), roads from dunkin-vs-starbucks/cache/roads.npz,
people from hospitals/cache/CenPop2020_Mean_TR.txt (Census 2020 tract centres; each tract takes
the stadium of its nearest timed road point). Shared stadiums (MetLife, SoFi) count for both
teams: their region is drawn in stripes of the two colours.

    python experiments/nfl/video.py --platform tiktok --stills 1,6,15,30,45,63
    pwsh experiments/render.ps1 nfl -Sound            # parts, workers, join + sound
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import math
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
sys.path.insert(0, str(HERE))
import reel_kit as K  # noqa: E402
from teams import STADIUMS, TEAMS  # noqa: E402

ROOT = HERE.parents[1]
CACHE, OUT, LOGOS = HERE / "cache", HERE / "out", HERE / "assets" / "logos"
ROADS = HERE.parent / "dunkin-vs-starbucks" / "cache" / "roads.npz"
TRACTS = HERE.parent / "hospitals" / "cache" / "CenPop2020_Mean_TR.txt"

K.SERIES = "Turf Wars"
K.WATER = (0, 0, 0)
K.ACCENT = K.INK
FPS, W, H = K.FPS, K.W, K.H
SECONDS = 65
RACE_START = K.HOOK + K.DIVE
CARD_IN = SECONDS - K.END_CARD - 0.8
RACE_END = CARD_IN - K.PULL_OUT
CLOCK_SHARE = 0.4
FADE_S = 0.5
LEVELS, FADE_FLOOR, FADE_POWER, SHIFT, CHUNK = 24, 0.30, 0.6, 4, 200_000
STRIPE_KM = 22                          # shared stadiums: stripe width of each team's colour
MIN_L = 58                              # Lab lightness floor: navy and brown vanish on black
CLOSE_DE = 28                           # neighbours closer than this (CIE76) get a different colour
STRIPE_DE = 55                          # a shared stadium's two stripes: much further apart (Rams/Chargers blues blurred)
MIN_BORDER = 4                          # road segments on a border before it counts (chimes, colours)
CHIME_GAP = 0.22                        # s: first meetings closer together than this share a chime

# --- projection (same Albers as dunkin-vs-starbucks/draw.py) -----------------------
R, LON0, P1, P2, PHI0 = 6371.0, -96.0, 29.5, 45.5, 23.0
_n = (math.sin(math.radians(P1)) + math.sin(math.radians(P2))) / 2
_C = math.cos(math.radians(P1)) ** 2 + 2 * _n * math.sin(math.radians(P1))
_rho0 = R * math.sqrt(_C - 2 * _n * math.sin(math.radians(PHI0))) / _n


def albers(lon, lat):
    lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    rho = R * np.sqrt(_C - 2 * _n * np.sin(lat)) / _n
    th = _n * (lon - math.radians(LON0))
    return rho * np.sin(th), _rho0 - rho * np.cos(th)


def hexrgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def lab(rgb) -> np.ndarray:
    return cv2.cvtColor(np.float32([[rgb]]) / 255, cv2.COLOR_RGB2LAB)[0, 0]


def from_lab(L, a, b) -> tuple[int, int, int]:
    rgb = cv2.cvtColor(np.float32([[[L, a, b]]]), cv2.COLOR_LAB2RGB)[0, 0]
    return tuple(int(round(v)) for v in np.clip(rgb, 0, 1) * 255)


def visible(rgb) -> tuple[int, int, int] | None:
    """The colour, lifted to MIN_L lightness for a black map; None for black/near-black greys."""
    L, a, b = lab(rgb)
    if L < 12 and math.hypot(a, b) < 12:
        return None
    return from_lab(max(L, MIN_L), a, b) if L < MIN_L else tuple(rgb)


# --- data ---------------------------------------------------------------------------

STATE_FIPS = {"39": "Ohio", "42": "Pennsylvania", "36": "New York", "12": "Florida", "48": "Texas",
              "06": "California", "55": "Wisconsin", "18": "Indiana", "37": "North Carolina",
              "47": "Tennessee", "26": "Michigan", "17": "Illinois", "24": "Maryland", "29": "Missouri",
              "08": "Colorado", "53": "Washington", "04": "Arizona", "32": "Nevada", "13": "Georgia",
              "22": "Louisiana", "27": "Minnesota", "25": "Massachusetts", "34": "New Jersey",
              "51": "Virginia", "45": "South Carolina", "01": "Alabama", "21": "Kentucky"}


def prepare() -> dict:
    stad = [r["chain"] for r in csv.DictReader(open(CACHE / "stores.csv", encoding="utf-8"))]
    st_ll = [(float(r["lon"]), float(r["lat"])) for r in csv.DictReader(open(CACHE / "stores.csv", encoding="utf-8"))]
    r, d = np.load(ROADS), np.load(CACHE / "drivetime.npz")
    assert list(d["chains"]) == stad
    lon, lat, start = r["lon"], r["lat"], r["start"]
    win, arr = d["winner"], d["arrival_s"]
    x, y = albers(lon, lat)
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
    ok = (st < 255) & np.isfinite(t)
    p0, p1, st, t = p0[ok].astype(np.float32), p1[ok].astype(np.float32), st[ok].astype(np.int16), t[ok].astype(np.float32)
    mid = (p0 + p1) / 2
    length = np.hypot(*(p1 - p0).T).astype(np.float64)

    # team per piece: the stadium's team, or a stripe of one of its two teams
    team_names = list(TEAMS)
    tid = {k: i for i, k in enumerate(team_names)}
    first = np.array([tid[STADIUMS[s][0]] for s in stad], np.int16)
    team = first[st]
    stripe = (np.floor((mid[:, 0] + mid[:, 1]) / STRIPE_KM).astype(np.int64) % 2) == 1
    for si, s in enumerate(stad):
        if len(STADIUMS[s]) == 2:
            m = (st == si) & stripe
            team[m] = tid[STADIUMS[s][1]]

    # borders: consecutive road points with different stadiums; first contact = later arrival
    wa, wb = win[a], win[b]
    bm = (wa != wb) & (wa < 255) & (wb < 255) & np.isfinite(arr[a]) & np.isfinite(arr[b])
    pair_t, pair_n, pair_xy = {}, collections.Counter(), {}
    for i0, i1, ta, tb, xm, ym in zip(wa[bm], wb[bm], arr[a][bm], arr[b][bm], mx[bm], my[bm]):
        k = (int(min(i0, i1)), int(max(i0, i1)))
        tt = float(max(ta, tb))
        pair_n[k] += 1
        if tt < pair_t.get(k, math.inf):
            pair_t[k], pair_xy[k] = tt, (float(xm), float(ym))
    pairs = sorted((pair_t[k], k, pair_xy[k], pair_n[k]) for k in pair_t if pair_n[k] >= MIN_BORDER)

    # colours: primary, unless a neighbour (by border length) is too close; then the secondary
    cand = {k: [c for c in (visible(hexrgb(v[1])), visible(hexrgb(v[2]))) if c] for k, v in TEAMS.items()}
    pick = {k: 0 for k in TEAMS}
    nb = collections.defaultdict(set)
    for _, (i, j), _, cnt in pairs:
        for ta in STADIUMS[stad[i]]:
            for tb in STADIUMS[stad[j]]:
                nb[ta].add(tb)
                nb[tb].add(ta)
    striped = {frozenset(STADIUMS[s]) for s in stad if len(STADIUMS[s]) == 2}
    for ta, tb in map(tuple, striped):                                      # stripes must differ too
        nb[ta].add(tb)
        nb[tb].add(ta)

    def need(ta, tb):
        return STRIPE_DE if frozenset((ta, tb)) in striped else CLOSE_DE

    def de(ta, tb):
        return float(np.linalg.norm(lab(cand[ta][pick[ta]]) - lab(cand[tb][pick[tb]])))
    # Fewest switches (Max: team colours "unless there are 2 very similar colors that collide"):
    # take the closest clashing pair, switch whichever of the two is left with the fewest clashes,
    # avoiding greys (they read as unclaimed road); each team switches at most once.
    def grey(ta):
        return math.hypot(*lab(cand[ta][pick[ta]])[1:]) < 15

    def clashes(ta):
        return sum(de(ta, tb) < need(ta, tb) for tb in nb[ta])
    switched = set()
    while True:
        bad = sorted((de(ta, tb) - need(ta, tb), ta, tb) for ta in TEAMS for tb in nb[ta] if ta < tb and de(ta, tb) < need(ta, tb))
        bad = [x for x in bad if {x[1], x[2]} - switched]
        if not bad:
            break
        _, ta, tb = bad[0]
        opts = []
        for t_ in (ta, tb):
            if t_ in switched or len(cand[t_]) < 2:
                continue
            pick[t_] = 1
            opts.append((clashes(t_) + (2 if grey(t_) else 0), t_))
            pick[t_] = 0
        if not opts:
            switched |= {ta, tb}
            continue
        _, t_ = min(opts)
        pick[t_] = 1
        switched.add(t_)
    colour = [cand[k][pick[k]] for k in team_names]
    clash = sorted({tuple(sorted((ta, tb))) for ta in TEAMS for tb in nb[ta] if de(ta, tb) < need(ta, tb)})

    # people: each tract takes the stadium of its nearest timed road point
    good = (win < 255) & np.isfinite(arr)
    tree = cKDTree(np.column_stack([x[good], y[good]]))
    gw = win[good]
    tl, tpop, tst = [], [], []
    with open(TRACTS, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if row["STATEFP"] in ("02", "15", "72"):
                continue
            tl.append((float(row["LONGITUDE"]), float(row["LATITUDE"])))
            tpop.append(int(row["POPULATION"]))
            tst.append(row["STATEFP"])
    tl = np.array(tl)
    tx, ty = albers(tl[:, 0], tl[:, 1])
    _, nn = tree.query(np.column_stack([tx, ty]))
    tw = gw[nn]
    tpop, tst = np.array(tpop, np.float64), np.array(tst)
    people = np.bincount(tw, weights=tpop, minlength=len(stad))
    states = {}
    for fp, name in STATE_FIPS.items():
        m = tst == fp
        if m.any():
            sh = np.bincount(tw[m], weights=tpop[m], minlength=len(stad)) / tpop[m].sum()
            states[name] = sh

    # logo anchors over time: centroid of reached road, snapped onto the stadium's own road
    samples = np.concatenate([[0, 300, 600, 1200], np.geomspace(1800, max(float(t.max()), 1801), 60)])
    anchors = np.zeros((len(stad), len(samples), 3), np.float32)        # x, y, area km2
    order = np.argsort(t, kind="stable")
    for si in range(len(stad)):
        sel = order[st[order] == si]
        ts_ = t[sel]
        mxy, ln = mid[sel].astype(np.float64), length[sel]
        cx_ = np.cumsum(mxy[:, 0] * ln)
        cy_ = np.cumsum(mxy[:, 1] * ln)
        cl = np.cumsum(ln)
        cell = np.floor(mxy / 20).astype(np.int64)
        ckey = cell[:, 0] * 1_000_000 + cell[:, 1]
        sx, sy = albers([st_ll[si][0]], [st_ll[si][1]])
        dec = np.arange(0, len(sel), max(1, len(sel) // 4000))
        for k, ts in enumerate(samples):
            m = int(np.searchsorted(ts_, ts, side="right"))
            if m < 20:
                anchors[si, k] = (sx[0], sy[0], 0)
                continue
            gx, gy = cx_[m - 1] / cl[m - 1], cy_[m - 1] / cl[m - 1]
            dd = dec[dec < m]
            j = dd[np.argmin((mxy[dd, 0] - gx) ** 2 + (mxy[dd, 1] - gy) ** 2)]
            area = len(np.unique(ckey[:m])) * 400.0
            anchors[si, k] = (mxy[j, 0], mxy[j, 1], area)

    # the last road reached: nearest town
    jl = int(np.argmax(t))
    lx, ly = mid[jl]
    db = sqlite3.connect(ROOT / "data" / "app" / "places.sqlite")
    towns = db.execute("select name, lon, lat from places where kind in ('town','village','city') and country='US'").fetchall()
    tx2, ty2 = albers([q[1] for q in towns], [q[2] for q in towns])
    near = towns[int(np.argmin((tx2 - lx) ** 2 + (ty2 - ly) ** 2))][0]
    sx, sy = albers([q[0] for q in st_ll], [q[1] for q in st_ll])
    bounds = (float(x.min()), float(y.min()), float(x.max()), float(y.max()))
    return dict(stad=stad, team_names=team_names, p0=p0, p1=p1, st=st, team=team, t=t, mid=mid, length=length,
                colour=colour, clash=clash, picks={k: pick[k] for k in TEAMS}, pairs=pairs, people=people,
                total=float(tpop.sum()), states=states, samples=samples, anchors=anchors, last=jl,
                last_town=near, sxy=np.column_stack([sx, sy]), bounds=bounds)


# --- camera -------------------------------------------------------------------------

# (lon, lat, frame width km) or None (the lower 48 in the map area)
VIEWS = {"DC": (-76.8, 39.1, 200), "Northeast": (-74.6, 41.2, 650), "Ohio": (-82.2, 40.6, 600),
         "Florida": (-82.3, 28.0, 900), "Texas": (-97.8, 31.4, 1200), "California": (-119.5, 36.2, 1300),
         "Great Lakes": (-87.6, 43.2, 1000), "West": (-110.5, 41.0, 2400), "US": None}
SHOTS = [(RACE_START, "DC"), (6.4, "DC"), (7.8, "Northeast"), (10.6, "Northeast"), (12.2, "Ohio"),
         (15.0, "Ohio"), (16.8, "Florida"), (19.6, "Florida"), (21.4, "Texas"), (24.0, "Texas"),
         (25.8, "California"), (28.6, "California"), (30.4, "Great Lakes"), (33.2, "Great Lakes"),
         (35.4, "West"), (38.6, "West"), (40.6, "US")]
# held view -> (kicker, state, teams in that state's split)
SPLITS = {"DC": ("Washington–Baltimore", "Maryland", ["BAL", "WAS"]),
          "Northeast": ("The Northeast", "New Jersey", ["NY", "PHI"]),
          "Ohio": ("Ohio", "Ohio", ["CLE", "CIN", "PIT"]),
          "Florida": ("Florida", "Florida", ["MIA", "TB", "JAX"]),
          "Texas": ("Texas", "Texas", ["DAL", "HOU"]),
          "California": ("California", "California", ["LA", "SF", "LV"]),
          "Great Lakes": ("Wisconsin", "Wisconsin", ["GB", "MIN", "CHI"]),
          "West": ("The West", None, None)}


def us_view(top, bottom, b):
    x0, y0, x1, y1 = b
    k = max((x1 - x0) * 1.04 / W, (y1 - y0) * 1.04 / (bottom - top))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return cx, cy + ((top + bottom) / 2 - K.MAP_CENTER_Y) * k, k


def view_of(name, b):
    if VIEWS[name] is None:
        return us_view(K.MAP_TOP, K.MAP_BOTTOM, b)
    lon, lat, width = VIEWS[name]
    x, y = albers([lon], [lat])
    return float(x[0]), float(y[0]), width / W


def move(a, b, k):
    k = k * k * (3 - 2 * k)
    la, lb = math.log(a[2]), math.log(b[2])
    need = math.hypot(b[0] - a[0], b[1] - a[1]) / (W * 0.8)
    bump = max(0.0, math.log(max(need, 1e-9)) - (la + lb) / 2)
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, math.exp(la + (lb - la) * k + bump * 4 * k * (1 - k)))


def camera(sec, b, card_view):
    if sec < K.HOOK:
        return view_of("US", b), "hook"
    if sec < RACE_START:
        return move(view_of("US", b), view_of(SHOTS[0][1], b), (sec - K.HOOK) / K.DIVE), None
    if sec > RACE_END:
        return move(view_of(SHOTS[-1][1], b), card_view, min(1.0, (sec - RACE_END) / K.PULL_OUT)), None
    for (s0, v0), (s1, v1) in zip(SHOTS, SHOTS[1:]):
        if sec <= s1:
            if v0 == v1:
                return view_of(v0, b), v0
            return move(view_of(v0, b), view_of(v1, b), (sec - s0) / (s1 - s0)), None
    return view_of(SHOTS[-1][1], b), SHOTS[-1][1]


def held_range(name):
    for (s0, v0), (s1, v1) in zip(SHOTS, SHOTS[1:]):
        if v0 == v1 == name:
            return s0, s1
    return SHOTS[-1][0], RACE_END


def schedule(ts, n):
    t_end = float(ts[-1])
    grid = np.linspace(0.0, t_end, 40_000)
    progress = CLOCK_SHARE * grid / t_end + (1 - CLOCK_SHARE) * np.searchsorted(ts, grid, side="right") / len(ts)
    return np.interp(np.linspace(0.0, 1.0, n), progress, grid)


def hm(seconds):
    m = int(seconds // 60)
    return f"{m // 60}:{m % 60:02d}"


def mil(p):
    return f"{p / 1e6:.1f} M"


# --- canvas -------------------------------------------------------------------------

class Canvas:
    def __init__(self, view):
        x, y, k = view
        self.s, self.ox, self.oy = 1 / k, W / 2 - x / k, K.MAP_CENTER_Y + y / k
        self.img = np.zeros((H, W, 3), np.uint8)

    def px(self, p):
        return np.column_stack([p[:, 0] * self.s + self.ox, self.oy - p[:, 1] * self.s])

    def add(self, D, sel, t_max, colours_bgr, thick=1, floor=FADE_FLOOR):
        if not len(sel):
            return
        f = np.clip(D["t"][sel] / t_max, 0, 1) ** FADE_POWER
        lvl = np.minimum((f * LEVELS).astype(np.int32), LEVELS - 1)
        nt = len(colours_bgr)
        key = (LEVELS - 1 - lvl) * nt + D["team"][sel]           # dim levels first, bright on top
        o = np.argsort(key, kind="stable")
        ks, sel = key[o], sel[o]
        cuts = np.flatnonzero(np.diff(ks)) + 1
        for grp_k, idx in zip(ks[np.concatenate([[0], cuts])], np.split(sel, cuts)):
            L, c = LEVELS - 1 - grp_k // nt, grp_k % nt
            kk = 1 - (1 - floor) * (L + 0.5) / LEVELS
            col = tuple(int(v * kk) for v in colours_bgr[c])
            for a in range(0, len(idx), CHUNK):
                j = idx[a:a + CHUNK]
                p = np.round(np.stack([self.px(D["p0"][j]), self.px(D["p1"][j])], 1) * (1 << SHIFT)).astype(np.int32)
                cv2.polylines(self.img, list(p.reshape(-1, 2, 1, 2)), False, col, thick, cv2.LINE_AA, SHIFT)


def close_up(view):
    z = min(1.0, max(0.0, (0.6 - view[2]) / 0.45))
    return (3 if view[2] < 0.25 else 2 if view[2] < 0.6 else 1), FADE_FLOOR + (0.7 - FADE_FLOOR) * z


# --- the reel -----------------------------------------------------------------------

class Reel:
    def __init__(self, platform):
        K.use(platform)
        D = self.D = prepare()
        self.order = np.argsort(D["t"], kind="stable")
        self.ts = D["t"][self.order]
        self.t_fade = float(np.percentile(self.ts, 99.5))
        self.bgr = [c[::-1] for c in D["colour"]]
        n = SECONDS * FPS
        self.secs = np.arange(n) / FPS
        r0, r1 = int(RACE_START * FPS), int(RACE_END * FPS)
        drive = np.zeros(n)
        drive[r0:r1 + 1] = schedule(self.ts, r1 + 1 - r0)
        drive[r1 + 1:] = self.ts[-1]
        self.drive = drive
        self.upto = np.searchsorted(self.ts, drive, side="right")
        self.upto[:r0] = 0
        # people reached, per stadium, as the clock runs (tract counting by road arrival is
        # what the end card uses; the HUD shows the share of the final total reached so far)
        self.card_bottom = self.end_card(Image.new("RGBA", (1, 1)), 0)
        self.card_view = us_view(self.card_bottom + 30, K.TEXT_FLOOR, D["bounds"])
        self.shade = K.shade()
        self.cv, self.view, self.done = None, None, 0
        self.hook_img = self.final = None
        self.us_k = view_of("US", D["bounds"])[2]
        self.logos = {k: Image.open(LOGOS / f"{v[3]}.png").convert("RGBA") for k, v in TEAMS.items()}
        self.logo_cache = {}
        sh = Image.open(HERE / "assets" / "nfl.png").convert("RGBA")
        sh = sh.crop(sh.getbbox())
        self.shield = sh.resize((round(sh.width * 300 / sh.height), 300), Image.LANCZOS)
        # cumulative people per stadium as roads are reached: tracts take their road's arrival
        self.chimes = self.chime_times()

    # --- data helpers
    def chime_times(self):
        out = []
        for tt, _, _, _ in self.D["pairs"]:
            i = int(np.searchsorted(self.drive[int(RACE_START * FPS):], tt))
            s = RACE_START + i / FPS
            if s < RACE_END and (not out or s - out[-1] >= CHIME_GAP):
                out.append(s)
        return out

    def ranking(self):
        """(people, label, colour, stadium) per stadium, most first."""
        D = self.D
        tid = {k: i for i, k in enumerate(D["team_names"])}
        rows = []
        for si, s in enumerate(D["stad"]):
            names = " & ".join(TEAMS[t][0] for t in STADIUMS[s])
            rows.append((float(D["people"][si]), names, D["colour"][tid[STADIUMS[s][0]]], s))
        return sorted(rows, reverse=True)

    # --- drawing
    def roads(self, view, upto):
        D = self.D
        x, y, k = view
        cy = y - (H / 2 - K.MAP_CENTER_Y) * k
        thick, floor = close_up(view)

        def in_view(idx):
            m = D["mid"][idx]
            return idx[(np.abs(m[:, 0] - x) < W * k * 0.52) & (np.abs(m[:, 1] - cy) < H * k * 0.52)]
        if self.cv is None or view != self.view or upto < self.done:
            self.cv = Canvas(view)
            self.cv.add(D, in_view(self.order[:upto]), self.t_fade, self.bgr, thick, floor)
        else:
            self.cv.add(D, in_view(self.order[self.done:upto]), self.t_fade, self.bgr, thick, floor)
        self.view, self.done = view, upto
        return self.cv.img[:, :, ::-1].copy()

    def logo(self, team, px_):
        key = (team, px_)
        if key not in self.logo_cache:
            self.logo_cache[key] = self.logos[team].resize((px_, px_), Image.LANCZOS)
        return self.logo_cache[key]

    def overlay_logos(self, im, view, drive_s, alpha=1.0):
        D = self.D
        x, y, k = view
        smp = D["samples"]
        for si, s in enumerate(D["stad"]):
            a = D["anchors"][si]
            ax = np.interp(drive_s, smp, a[:, 0])
            ay = np.interp(drive_s, smp, a[:, 1])
            area = np.interp(drive_s, smp, a[:, 2])
            size_km = 0.55 * math.sqrt(max(area, 1.0))
            size = int(np.clip(size_km / k, 0, 210 if len(STADIUMS[s]) == 1 else 150))
            if size < 34:
                continue
            u, v = (ax - x) / k + W / 2, (y - ay) / k + K.MAP_CENTER_Y
            teams = STADIUMS[s]
            for q, t in enumerate(teams):
                du = (q - (len(teams) - 1) / 2) * size * 1.02
                lg = self.logo(t, size)
                if alpha < 1:
                    lg = lg.copy()
                    lg.putalpha(lg.getchannel("A").point(lambda vv: int(vv * alpha)))
                px0, py0 = int(u + du - size / 2), int(v - size / 2)
                if -size < px0 < W and -size < py0 < H:
                    im.alpha_composite(lg, (max(0, px0), max(0, py0)),
                                       (max(0, -px0), max(0, -py0)))

    def stadium_dots(self, img, view):
        x, y, k = view
        for (sx, sy) in self.D["sxy"]:
            u, v = (sx - x) / k + W / 2, (y - sy) / k + K.MAP_CENTER_Y
            if -10 < u < W + 10 and -10 < v < H + 10:
                cv2.circle(img, (int(u * 16), int(v * 16)), 5 * 16, (255, 255, 255), -1, cv2.LINE_AA, 4)
        return img

    # --- text
    def end_rows(self):
        rk = self.ranking()
        rows = [(mil(p), f"{lab_}: most people closest", col) for p, lab_, col, _ in rk[:1]]
        rows += [(mil(p), lab_, col) for p, lab_, col, _ in rk[1:3]]
        p, lab_, col, _ = rk[-1]
        rows.append((mil(p), f"{lab_}: the fewest", col))
        return rows, ["Each road and Census 2020 tract goes to the stadium that's the shortest drive, no traffic",
                      "Roads: OpenStreetMap   Routing: OSRM"]

    def end_card(self, im, alpha):
        rows, footer = self.end_rows()
        top = K.TOP + 160
        bottom = top + 40 + len(rows) * 110 + 40 + 36 * (len(footer) + 1) + 30
        if alpha <= 0:
            return bottom
        layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((50, top, W - 50, bottom), 28, fill=K.CARD_FILL, outline=K.BORDER, width=2)
        for r, (big, label, col) in enumerate(rows):
            yy = top + 40 + r * 110
            d.text((96, yy), big, font=K.F("card"), fill=col)
            d.text((W - 96, yy + 34), label, font=K.F("small"), fill=K.MUTED, anchor="rm")
        yy = top + 40 + len(rows) * 110 + 30
        for line in footer:
            d.text((W / 2, yy), line, font=K.F("small"), fill=K.MUTED, anchor="mt")
            yy += 36
        d.text((W / 2, yy + 4), f"{K.SERIES}  ·  {K.HANDLE}", font=K.F("label"), fill=K.INK, anchor="mt")
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
        im.alpha_composite(layer)
        return bottom

    def split_label(self, held):
        kick, state, codes = SPLITS[held]
        D = self.D
        if state is None:                                    # the West: biggest regions by area
            area = {s: float(D["anchors"][si, -1, 2]) for si, s in enumerate(D["stad"])}
            big = sorted(area, key=area.get, reverse=True)[:2]
            nm = [" & ".join(TEAMS[t][0] for t in STADIUMS[s]) for s in big]
            return kick, f"{nm[0]} and {nm[1]}", "the two biggest regions by area"
        sh = D["states"][state]
        parts = []
        for s in codes:
            si = D["stad"].index(s)
            nm = " & ".join(TEAMS[t][0] for t in STADIUMS[s])
            if sh[si] >= 0.01:                                 # "Raiders 0%" in California said nothing
                parts.append(f"{nm} {round(100 * sh[si])}%")
        return kick, " · ".join(parts), f"of {state}'s people, by closest stadium"

    def hud(self, im, i, held):
        sec = self.secs[i]
        d = ImageDraw.Draw(im)
        card_a = K.smoothstep((sec - CARD_IN) / 0.8)
        K.header(d, "Every NFL stadium", "Each road is colored by the closest stadium")
        if card_a < 1:
            met = sum(1 for p in self.D["pairs"] if p[0] <= self.drive[i]) if sec >= RACE_START else 0
            up = int(self.upto[i]) if sec >= RACE_START else 0
            for c, (big, unit) in enumerate([(hm(self.drive[i]), "drive time"),
                                             (f"{round(100 * up / len(self.ts))}%", "of roads reached"),
                                             (f"{met}", "borders met")]):
                xx = W * (c + 0.5) / 3
                d.text((xx, K.TOP + 175), big, font=K.F("big"), fill=K.INK, anchor="mt")
                d.text((xx, K.TOP + 270), unit, font=K.F("unit"), fill=K.MUTED, anchor="mt")
        K.watermark(d)
        if held in SPLITS and RACE_START <= sec <= RACE_END:
            a, b = held_range(held)
            al = min(1.0, (sec - a) / 0.4, (b - sec) / 0.4)
            if al > 0:
                layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
                K.now_label(ImageDraw.Draw(layer), *self.split_label(held))
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * al)))
                im.alpha_composite(layer)
        if int(self.upto[i]) >= len(self.ts) and sec <= CARD_IN:
            j = self.D["last"]
            (vx, vy, vk), _ = camera(sec, self.D["bounds"], self.card_view)
            xx = (self.D["mid"][j, 0] - vx) / vk + W / 2
            yy = (vy - self.D["mid"][j, 1]) / vk + K.MAP_CENTER_Y
            al = min(1.0, (sec - self.secs[np.argmax(self.upto >= len(self.ts))]) / 0.2)
            over = Image.new("RGBA", im.size, (0, 0, 0, 0))
            K.callout(ImageDraw.Draw(over), xx, yy, f"Last road: {hm(self.ts[-1])}, near {self.D['last_town']}", al)
            im.alpha_composite(over)
        hook_a = 1 - K.smoothstep((sec - (K.HOOK - 0.3)) / 0.5)
        if hook_a > 0:                       # the question, under the NFL shield (Max, 2026-10-16); the answer is the end card
            from PIL import ImageFont
            layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
            dd = ImageDraw.Draw(layer)
            a = int(255 * hook_a)
            lines = ["Which NFL team is", "closest to the", "most Americans?"]
            f0 = K.F("hook")
            size = f0.size
            while max(f0.font_variant(size=size).getlength(t) for t in lines) > W - 90:
                size -= 4
            f = f0.font_variant(size=size)
            lh = int(size * 1.12)
            y0 = K.TEXT_FLOOR - 200 - len(lines) * lh / 2
            dd.rectangle((0, y0 - 40, W, y0 + len(lines) * lh + 40), fill=(0, 0, 0, int(170 * hook_a)))
            for r, t in enumerate(lines):
                dd.text((W / 2, y0 + r * lh), t, font=f, fill=(*(K.INK if r < 2 else (255, 182, 18)), a), anchor="mt")
            lg = self.shield
            if hook_a < 1:
                lg = lg.copy()
                lg.putalpha(lg.getchannel("A").point(lambda v: int(v * hook_a)))
            layer.alpha_composite(lg, ((W - lg.width) // 2, int(max(K.MAP_TOP + 10, y0 - 60 - lg.height))))
            im.alpha_composite(layer)
        self.end_card(im, card_a)

    def frame(self, i):
        sec = self.secs[i]
        D = self.D
        view, held = camera(sec, D["bounds"], self.card_view)
        if held == "hook":
            if self.hook_img is None:
                self.hook_img = self.roads(view, len(self.ts))
            img = self.hook_img.copy()
            drive_s = float(self.ts[-1])
        elif sec > RACE_END and view[2] > 0.5 * self.us_k:
            if self.final is None:
                ux, uy, uk = view_of("US", D["bounds"])
                big = Canvas((ux, uy, uk / 2))
                big.img = np.zeros((2 * H, 2 * W, 3), np.uint8)
                big.ox, big.oy = W - ux * 2 / uk, 2 * K.MAP_CENTER_Y + uy * 2 / uk
                big.add(D, self.order, self.t_fade, self.bgr, 2)
                self.final = big.img[:, :, ::-1].copy()
            (ux, uy, uk), (x, y, k) = view_of("US", D["bounds"]), view
            sc = uk / (2 * k)
            m = np.float32([[sc, 0, W / 2 - uk / k * W / 2 + (ux - x) / k],
                            [0, sc, K.MAP_CENTER_Y - uk / k * K.MAP_CENTER_Y + (y - uy) / k]])
            img = cv2.warpAffine(self.final, m, (W, H), flags=cv2.INTER_AREA)
            drive_s = float(self.ts[-1])
        else:
            img = self.roads(view, int(self.upto[i]))
            fa = 1 - (sec - K.HOOK) / FADE_S
            if 0 < fa <= 1 and self.hook_img is not None:
                img = (img * (1 - fa) + self.hook_img * fa).astype(np.uint8)
            drive_s = float(self.drive[i])
        img = self.stadium_dots(img, view)
        im = Image.fromarray(img).convert("RGBA")
        self.overlay_logos(im, view, drive_s)
        im.alpha_composite(self.shade)
        self.hud(im, i, held)
        return im.convert("RGB").tobytes()


# --- render -------------------------------------------------------------------------

def render_range(args):
    platform, a, b, path = args
    reel = Reel(platform)
    ff = K.encoder(path)
    for i in range(a, b):
        ff.stdin.write(reel.frame(i))
    ff.stdin.close()
    return ff.wait()


def concat(parts, out):
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{q.name}'\n" for q in parts))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)],
                   check=True)
    lst.unlink()
    for q in parts:
        q.unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["tiktok", "reels"], default="tiktok")
    ap.add_argument("--stills", help="comma-separated seconds; writes PNGs only")
    ap.add_argument("--workers", type=int, default=3, help="each draws a contiguous run of frames (the canvas is incremental)")
    ap.add_argument("--part", help="k/n")
    ap.add_argument("--join", type=int)
    ap.add_argument("--sound", action="store_true", help="a chime at each first meeting, a chord at the end")
    ap.add_argument("--report", action="store_true", help="print the numbers and colour picks")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    slug = f"turf_wars_nfl_{args.platform}"
    if args.report:
        reel = Reel(args.platform)
        for p, lab_, col, s in reel.ranking():
            print(f"{s:4} {lab_:22} {p / 1e6:6.2f} M  {100 * p / reel.D['total']:5.1f}%  {col}")
        print("colour picks (1 = secondary):", {k: v for k, v in reel.D["picks"].items() if v})
        print("still close:", reel.D["clash"])
        print(f"{len(reel.D['pairs'])} borders; first {[(round(p[0] / 60), reel.D['stad'][p[1][0]], reel.D['stad'][p[1][1]]) for p in reel.D['pairs'][:6]]}")
        print(f"{len(reel.chimes)} chimes; last road {hm(reel.ts[-1])} near {reel.D['last_town']}")
        for name in SPLITS:
            print(name, reel.split_label(name))
        return
    if args.stills:
        reel = Reel(args.platform)
        for sec in map(float, args.stills.split(",")):
            reel.cv = None
            i = min(int(sec * FPS), SECONDS * FPS - 1)
            Image.frombytes("RGB", (W, H), reel.frame(i)).save(OUT / f"{args.platform}_{sec:04.1f}.png")
        return
    n = SECONDS * FPS
    silent = OUT / f"{slug}_silent.mp4"
    if args.join:
        concat([OUT / f"{slug}_part{k}.mp4" for k in range(1, args.join + 1)], silent)
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
        concat(subs, silent)
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
