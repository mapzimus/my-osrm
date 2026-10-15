"""Every road's nearest shore, drawn like river basins (idea #224), the 65 s social cut.

Four shores (Max, 2026-10-15): Atlantic, Gulf of Mexico, Pacific, Great Lakes.
Every drivable shore point (inventory.py) "leaves" at once; each road lights up in
the colour of the shore that reaches it first, brightest near the water, fading with
drive time. Built from experiments/airports/reel.py (same engine, camera and stats).
Colours: dataviz dark slots blue, amber, aqua + a pale lavender for the Great Lakes
(lightness separates it); all pairs pass CVD (worst dE 15.6) and normal vision (20.9).

    python experiments/coasts/reel.py --platform tiktok --stills 1,6,20,30,50,57,63
    python experiments/coasts/reel.py --platform tiktok

Writes out/coasts_<platform>.mp4 (silent; add audio in the app).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
CACHE, OUT = HERE / "cache", HERE / "out"
TURF = HERE.parent / "dunkin-vs-starbucks"
sys.path[:0] = [str(HERE.parent), str(TURF)]
import draw  # noqa: E402
import reel_kit as K  # noqa: E402
from draw import Canvas, albers  # noqa: E402

K.WATER = (0, 0, 0)                     # black map: the canvas the roads are drawn on
FPS, W, H = K.FPS, K.W, K.H
SECONDS = 65
RACE_START = K.HOOK + K.DIVE
CARD_IN = SECONDS - K.END_CARD - 0.8
FINAL_HOLD = 6.0                        # the finished US map, no card (Max, 2026-10-15: "show the final map longer")
LAST_HOLD = 2.5                         # seconds on the farthest road after it's reached
PULL = CARD_IN - FINAL_HOLD - K.PULL_OUT    # leave the farthest road for the whole map
REACHED = PULL - LAST_HOLD              # the clock reaches the farthest road here
CLOCK_SHARE = 0.7                       # most roads are close: pace mostly by the clock so the warm bands
                                        # arrive during the regional tour
FADE_S = 0.5

SHORES = ["Atlantic", "Gulf", "Pacific", "Great Lakes"]
RAMP = [(0x39, 0x87, 0xE5), (0xFA, 0xB2, 0x19), (0x19, 0x9E, 0x70), (0xD9, 0xD2, 0xFB)]   # same order as SHORES
BAND_LABELS = ["Atlantic", "Gulf", "Pacific", "Great Lakes"]
SHORE_TEXT = {"Atlantic": "the Atlantic", "Gulf": "the Gulf", "Pacific": "the Pacific", "Great Lakes": "the Great Lakes"}
FADE = 0.3                              # brightness of the slowest roads (Turf Wars: bright near the water)
DENVER = (-104.99, 39.74)
DOT = (236, 239, 244)

VIEWS = {"Boston": (-71.08, 42.33, 120), "Chicago": (-87.9, 41.9, 300), "Gulf Coast": (-91.0, 30.6, 750),
         "Appalachia": (-81.6, 37.6, 750), "Mountain West": (-113.5, 41.0, 1300), "Great Plains": (-98.5, 42.5, 1300),
         "US": None}
# (second, view): hold where two keys name the same view, move between. "Last" = the farthest road.
# Denver is the finale: its roads light up at ~15:40 on the clock, and the farthest road (Larimer County)
# is next door, so the last shot frames both (LAST_KM).
SHOTS = [(RACE_START, "Boston"), (6.3, "Boston"), (7.9, "Chicago"), (10.2, "Chicago"), (12.0, "Gulf Coast"),
         (14.6, "Gulf Coast"), (16.3, "Appalachia"), (18.8, "Appalachia"), (20.8, "Mountain West"),
         (24.0, "Mountain West"), (26.0, "Great Plains"), (29.5, "Great Plains"), (31.8, "US"), (36.0, "US"),
         (39.0, "Last")]
KICKERS = {"Boston": "Boston", "Chicago": "Chicago", "Appalachia": "Appalachia"}
LAST_KM = 320                            # frame width on the farthest road (wide enough for Denver too)
DETOUR = 3.0                             # road_ok(): drive time / straight-line estimate above this is a map error
# road_ok(): (south, north, west, east, drop only over this many hours), why. Checked 2026-10-15.
EXCLUDE = [((41.05, 42.0, -115.9, -114.3, 1.5), "NE Elko County, NV: Deeth-Charleston Road's I-80 link is "
            "missing in OSM, so Jarbidge/Charleston/Contact roads route round through Idaho (5.7 h vs ~3 h)"),
           ((36.6, 37.4, -116.6, -115.8, 0.0), "Nevada Test Site: roads closed to the public")]
STATES = {"01": "AL", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC",
          "12": "FL", "13": "GA", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
          "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO",
          "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC",
          "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
          "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
          "56": "WY"}


def band(t_s: np.ndarray) -> np.ndarray:
    return np.searchsorted(np.array(BANDS) * 60.0, t_s, side="right").astype(np.int8)


# --- data -------------------------------------------------------------------------

def road_ok(lon: np.ndarray, lat: np.ndarray, arrival: np.ndarray) -> np.ndarray:
    """Road points to keep, cached. Out: points in Mexico or Canada (roads.py keeps 5 km past the
    border for piers and coastline), and broken links in the map data: a drive more than DETOUR x
    the straight line at 50 km/h (+15 min). Deeth-Charleston Road, NV, is 25 min from Elko but its
    I-80 link is missing in OSM, so the engine went 279 mi round through Idaho (5.85 h).
    Real slow roads sit at 1.0-1.5x; the cut takes 0.01% of points."""
    path = CACHE / "road_ok.npy"
    if path.exists():
        return np.load(path)
    import shapefile
    import shapely
    from shapely.geometry import shape
    # Home = inside the lower 48 by Census TIGER (full resolution), no slack: 50 m still let a Rio Grande road in Chihuahua through.
    # Natural Earth's coarse Canada outline let Saskatchewan roads through (airports, 2026-10-15), and
    # 300 m of slack still kept the Canadian end of the Opheim, MT border-crossing road (49.002 N).
    sf = shapefile.Reader(str(HERE.parent / "border_crossings" / "cache" / "states" / "tl_2024_us_state.shp"))
    us = shapely.union_all([shape(s.__geo_interface__) for s, r in zip(sf.shapes(), sf.records())
                            if r["STUSPS"] not in ("AK", "HI", "PR", "GU", "VI", "AS", "MP")])
    shapely.prepare(us)
    lo64, la64 = lon.astype(np.float64), lat.astype(np.float64)
    near_edge = np.ones(len(lon), bool)                  # only test points near the outline: the rest are inside
    core = shapely.buffer(us, -0.05)
    shapely.prepare(core)
    near_edge = ~shapely.contains_xy(core, lo64, la64)
    home = np.ones(len(lon), bool)
    home[near_edge] = shapely.contains_xy(us, lo64[near_edge], la64[near_edge])

    def xyz(lo, la):
        lo, la = np.radians(lo), np.radians(la)
        return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]) * 6371.0
    hx, hy = [], []
    with open(CACHE / "stores.csv", encoding="utf-8") as fh:
        for s in csv.DictReader(fh):
            hx.append(float(s["lon"]))
            hy.append(float(s["lat"]))
    crow, _ = cKDTree(xyz(np.array(hx), np.array(hy))).query(xyz(lon.astype(np.float64), lat.astype(np.float64)))
    sane = arrival / 3600 <= DETOUR * (crow / 50 + 0.25)
    lo, la, h = lon.astype(np.float64), lat.astype(np.float64), arrival / 3600
    for (s, n, w, e, over_h), why in EXCLUDE:
        sane &= ~((la > s) & (la < n) & (lo > w) & (lo < e) & (h > over_h))
    ok = home & sane & np.isfinite(arrival)
    print(f"road points dropped: {(~home).sum():,} abroad, {(home & ~sane).sum():,} broken links", flush=True)
    np.save(path, ok)
    return ok


def pieces(data: dict) -> dict:
    """draw.pieces for any number of chains (it keeps only 0 and 1): half-segments, km."""
    lon, lat, start = data["lon"], data["lat"], data["start"]
    last = np.zeros(len(lon), bool)
    last[start[1:] - 1] = True
    a = np.flatnonzero(~last[:-1])
    b = a + 1
    x, y = albers(lon, lat)
    mx, my = (x[a] + x[b]) / 2, (y[a] + y[b]) / 2
    win, arr = data["winner"], data["arrival"]
    ch = np.concatenate([win[a], win[b]])
    t = np.concatenate([arr[a], arr[b]])
    ok = (ch < len(RAMP)) & np.isfinite(t)
    p0 = np.column_stack([np.concatenate([x[a], mx]), np.concatenate([y[a], my])])
    p1 = np.column_stack([np.concatenate([mx, x[b]]), np.concatenate([my, y[b]])])
    return dict(p0=p0[ok].astype(np.float32), p1=p1[ok].astype(np.float32), chain=ch[ok].astype(np.int8),
                t=t[ok].astype(np.float32), bounds=(x.min(), y.min(), x.max(), y.max()))


def prepare() -> dict:
    r, d = np.load(TURF / "cache" / "roads.npz"), np.load(CACHE / "drivetime.npz")
    idx = np.array([SHORES.index(str(c)) for c in d["chains"]] + [255], np.uint8)   # drivetime order -> SHORES
    winner = idx[np.where(d["winner"] < 255, d["winner"], len(d["chains"]))]
    arrival = np.where(road_ok(r["lon"], r["lat"], d["arrival_s"]), d["arrival_s"], np.nan).astype(np.float32)
    data = dict(lon=r["lon"], lat=r["lat"], start=r["start"], winner=winner, arrival=arrival)
    pc = pieces(data)
    order = np.argsort(pc["t"], kind="stable")
    ts = pc["t"][order]
    mx, my = ((pc["p0"] + pc["p1"]) / 2).T
    last = int(order[-1])

    # people: each tract centre takes the shore and drive time of its nearest reached road point
    ok = np.isfinite(data["arrival"])
    rx, ry = albers(data["lon"][ok], data["lat"][ok])
    tree = cKDTree(np.column_stack([rx, ry]))
    st, pop, tlon, tlat = [], [], [], []
    with open(CACHE / "CenPop2020_Mean_TR.txt", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if row["STATEFP"] in STATES:
                st.append(STATES[row["STATEFP"]])
                pop.append(int(row["POPULATION"]))
                tlon.append(float(row["LONGITUDE"]))
                tlat.append(float(row["LATITUDE"]))
    px, py = albers(tlon, tlat)
    _, nn = tree.query(np.column_stack([px, py]))
    pt = data["arrival"][ok][nn].astype(np.float64)
    shore = data["winner"][ok][nn].astype(int)
    pop, st = np.array(pop, np.float64), np.array(st)
    by_t = np.argsort(pt)
    people = dict(t=pt[by_t], cum=np.concatenate([[0.0], np.cumsum(pop[by_t])]), band=shore[by_t],
                  total=float(pop.sum()))
    share = {SHORES[k]: float(pop[shore == k].sum() / pop.sum()) for k in range(len(SHORES))}

    region = {}                                 # (top shore, its share of people in frame, longest drive (s))
    for name, v in VIEWS.items():
        if v:
            x, y = albers([v[0]], [v[1]])
            half = v[2] / 2
            m = (np.abs(px - x[0]) < half) & (np.abs(py - y[0]) < half)
            r_in = (np.abs(mx - x[0]) < half) & (np.abs(my - y[0]) < half)
            sh = np.array([pop[m & (shore == k)].sum() for k in range(len(SHORES))])
            k1, k2 = np.argsort(-sh)[:2]
            tot = max(1.0, sh.sum())
            region[name] = (SHORES[k1], 100 * sh[k1] / tot, SHORES[k2], 100 * sh[k2] / tot)
    # Denver: drive time to each shore from the nearest road point (drivetime's per-chain times)
    dx, dy = albers([DENVER[0]], [DENVER[1]])
    ax, ay = albers(data["lon"], data["lat"])
    j = int(np.nanargmin(np.where(ok, (ax - dx[0]) ** 2 + (ay - dy[0]) ** 2, np.inf)))
    denver = sorted((float(d["times"][c, j]), SHORES[idx[c]]) for c in range(len(d["chains"])))
    lon_l, lat_l = lonlat_of(mx[last], my[last])
    return dict(pc=pc, order=order, ts=ts, mx=mx, my=my, last=last, people=people, share=share,
                region=region, denver=denver, place=place_name(lon_l, lat_l),
                t_fade=float(np.percentile(ts, 99.5)))


def shore_points():
    lon, lat = [], []
    with open(CACHE / "stores.csv", encoding="utf-8") as fh:
        for s in csv.DictReader(fh):
            lon.append(float(s["lon"]))
            lat.append(float(s["lat"]))
    return albers(lon, lat)


def lonlat_of(x: float, y: float) -> tuple[float, float]:
    """Inverse of albers(), exact to ~1 m (a ~5 km grid search put the Opheim, MT road in Canada)."""
    sys.path.insert(0, str(HERE.parent / "border_crossings"))
    from search import inverse
    lon, lat = inverse(np.array([x]), np.array([y]))
    return float(lon[0]), float(lat[0])


def place_name(lon: float, lat: float) -> str:
    """'near Town, ST' from Nominatim, cached."""
    path = CACHE / "places.json"
    cache = json.load(open(path)) if path.exists() else {}
    key = f"{lon:.3f},{lat:.3f}"
    if key not in cache:
        q = urllib.parse.urlencode(dict(lat=lat, lon=lon, format="json", zoom=10, addressdetails=1))
        req = urllib.request.Request(f"https://nominatim.openstreetmap.org/reverse?{q}",
                                     headers={"User-Agent": "my-osrm coasts video (mapzimus)"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            a = json.load(resp).get("address", {})
        town = a.get("city") or a.get("town") or a.get("village") or a.get("hamlet") or a.get("county", "")
        town_only = a.get("city") or a.get("town") or a.get("village") or a.get("hamlet")
        cache[key] = (f"near {town}, {a.get('state', '')}" if town_only           # no town nearby: just the county
                      else f"{town}, {a.get('state', '')}").strip(", ")
        json.dump(cache, open(path, "w"), indent=0)
    if re.match(r"near T\d+ R\d+", cache[key]):          # Maine's unorganized townships: "T8 R16 WELS"
        return "North Maine Woods"
    if cache[key] in ("near Valley County, Montana", "Valley County, Montana") and lat > 48.95:     # MT-24 at the Port of Opheim (checked)
        return "Opheim, Montana, at the Canadian border"
    return cache[key]


def schedule(ts: np.ndarray, n: int) -> np.ndarray:
    """Drive time (s) for n race frames: a blend of the clock and the share of roads reached runs linearly."""
    t_end = float(ts[-1])
    grid = np.linspace(0.0, t_end, 40_000)
    progress = CLOCK_SHARE * grid / t_end + (1 - CLOCK_SHARE) * np.searchsorted(ts, grid, side="right") / len(ts)
    return np.interp(np.linspace(0.0, 1.0, n), progress, grid)


# --- camera -----------------------------------------------------------------------

def us_view(top: float, bottom: float, pc) -> tuple[float, float, float]:
    x0, y0, x1, y1 = pc["bounds"]
    k = max((x1 - x0) * 1.04 / W, (y1 - y0) * 1.04 / (bottom - top))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return cx, cy + ((top + bottom) / 2 - K.MAP_CENTER_Y) * k, k


def move(a, b, k: float) -> tuple[float, float, float]:
    """Smoothstep from view a to b; long moves zoom out on the way so both ends stay in sight."""
    k = k * k * (3 - 2 * k)
    la, lb = math.log(a[2]), math.log(b[2])
    need = math.hypot(b[0] - a[0], b[1] - a[1]) / (W * 0.8)
    bump = max(0.0, math.log(max(need, 1e-9)) - (la + lb) / 2)
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, math.exp(la + (lb - la) * k + bump * 4 * k * (1 - k)))


def floor_for(view) -> float:
    """Fade floor: FADE on the national map; close up, the late (interior) roads would all sit at the
    floor and look muddy, so they get brighter (Denver's three-way meeting, 2026-10-15)."""
    return FADE if view[2] > 0.35 else 0.8


def close_up(view) -> int:
    return 2 if view[2] < 0.35 else 1


def held_range(name: str) -> tuple[float, float]:
    for (s0, v0), (s1, v1) in zip(SHOTS, SHOTS[1:]):
        if v0 == v1 == name:
            return s0, s1
    return SHOTS[-1][0], PULL


def hm(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60}:{m % 60:02d}"


def hm_long(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def millions(n: float) -> str:
    return f"{n / 1e6:.1f} million"


def HOOK_LINES(d: dict) -> list[str]:
    return ["Denver is a", "three-way tie"] if d["denver"][2][0] - d["denver"][0][0] < 3600 else \
        [pct_down(max(d["share"].values())), "of Americans"]


def HOOK_SUB(d: dict) -> str:
    (t1, s1), (t2, s2), (t3, s3) = d["denver"][:3]
    return (f"{s1}, {s2} and {s3} are all {hm(t1)} to {hm(t3)} away" if t3 - t1 < 3600
            else f"are closest to {SHORE_TEXT[max(d['share'], key=d['share'].get)]}")


def pct_down(share: float) -> str:
    """Rounded down, tenths above 99%: '100%' only when it is (Max, 2026-10-15)."""
    if share >= 1 - 1e-12:
        return "100%"
    if share >= 0.99:
        return f"{math.floor(share * 1000) / 10:.1f}%"
    return f"{math.floor(share * 100)}%"


# --- frames -----------------------------------------------------------------------

class Reel:
    def __init__(self, platform: str):
        K.use(platform)
        draw.COLOURS[:] = RAMP
        self.d = prepare()
        self.d["hx"], self.d["hy"] = shore_points()
        pc = self.d["pc"]
        n = SECONDS * FPS
        self.secs = np.arange(n) / FPS
        r0, r1 = int(RACE_START * FPS), int(REACHED * FPS)
        drive = np.zeros(n)
        drive[r0:r1 + 1] = schedule(self.d["ts"], r1 + 1 - r0)
        drive[r1 + 1:] = self.d["ts"][-1]
        self.drive = drive
        self.upto = np.searchsorted(self.d["ts"], drive, side="right")
        self.upto[:r0] = 0
        self.card_bottom = self.end_card(Image.new("RGBA", (1, 1)), 0)
        self.card_view = us_view(self.card_bottom + 30, K.TEXT_FLOOR, pc)
        self.shade = K.shade()
        self.cv, self.view, self.done = None, None, 0
        self.hook_img, self.final, self.final_done = None, None, 0
        self.us_k = self.view_of("US")[2]

    def view_of(self, name: str) -> tuple[float, float, float]:
        if name == "Last":
            j = self.d["last"]
            return float(self.d["mx"][j]), float(self.d["my"][j]), LAST_KM / W
        if VIEWS[name] is None:
            return us_view(K.MAP_TOP, K.MAP_BOTTOM, self.d["pc"])
        lon, lat, width = VIEWS[name]
        x, y = albers([lon], [lat])
        return float(x[0]), float(y[0]), width / W

    def camera(self, sec: float) -> tuple[tuple, str | None]:
        if sec < K.HOOK:
            return self.view_of("US"), "hook"
        if sec < RACE_START:
            return move(self.view_of("US"), self.view_of(SHOTS[0][1]), (sec - K.HOOK) / K.DIVE), None
        if sec > CARD_IN:                                   # the finished map makes room for the card
            return move(self.view_of("US"), self.card_view, min(1.0, (sec - CARD_IN) / 0.8)), None
        if sec > PULL + K.PULL_OUT:
            return self.view_of("US"), "final"
        if sec > PULL:
            return move(self.view_of(SHOTS[-1][1]), self.view_of("US"), (sec - PULL) / K.PULL_OUT), None
        for (s0, v0), (s1, v1) in zip(SHOTS, SHOTS[1:]):
            if sec <= s1:
                if v0 == v1:
                    return self.view_of(v0), v0
                return move(self.view_of(v0), self.view_of(v1), (sec - s0) / (s1 - s0)), None
        return self.view_of(SHOTS[-1][1]), SHOTS[-1][1]

    def dots(self, img: np.ndarray, view) -> np.ndarray:
        """Every drivable shore point as a faint dot: it traces the water's edge."""
        x, y, k = view
        px = (self.d["hx"] - x) / k + W / 2
        py = (y - self.d["hy"]) / k + K.MAP_CENTER_Y
        m = (px > -5) & (px < W + 5) & (py > -5) & (py < H + 5)
        zoom = math.log2(max(self.us_k / k, 1.0))
        r = float(np.clip(0.9 + 0.9 * zoom, 0.9, 5.0))
        col = tuple(int(c * min(1.0, 0.3 + 0.35 * zoom)) for c in DOT)      # dim at US scale: the roads are the story
        layer = img.copy()
        for u, v in zip(px[m], py[m]):
            if r >= 2.5:                                                    # 2px surface ring once dots are big
                cv2.circle(layer, (int(u * 16), int(v * 16)), int((r + 2) * 16), (0, 0, 0), -1, cv2.LINE_AA, 4)
            cv2.circle(layer, (int(u * 16), int(v * 16)), int(r * 16), col, -1, cv2.LINE_AA, 4)
        return layer

    def in_view(self, idx, view):
        x, y, k = view
        mx, my = self.d["mx"], self.d["my"]
        cy = y - (H / 2 - K.MAP_CENTER_Y) * k
        return idx[(np.abs(mx[idx] - x) < W * k * 0.52) & (np.abs(my[idx] - cy) < H * k * 0.52)]

    def roads(self, view, upto: int) -> np.ndarray:
        order, pc, tf = self.d["order"], self.d["pc"], self.d["t_fade"]
        if self.cv is None or view != self.view or upto < self.done:
            self.cv = Canvas((0, 0, 1, 1), W, H)
            x, y, k = view
            self.cv.s, self.cv.ox, self.cv.oy = 1 / k, W / 2 - x / k, K.MAP_CENTER_Y + y / k
            self.cv.add(pc, self.in_view(order[:upto], view), tf, close_up(view), floor_for(view))
        else:
            self.cv.add(pc, self.in_view(order[self.done:upto], view), tf, close_up(view), floor_for(view))
        self.view, self.done = view, upto
        return self.cv.img[:, :, ::-1].copy()

    # --- stats ---
    def people_bar(self, d: ImageDraw.ImageDraw, drive_s: float) -> None:
        """Part-to-whole: share of people reached so far, one segment per band (2px surface gaps),
        legend of swatches below (identity is never colour alone)."""
        p = self.d["people"]
        x0, x1, y0 = 36, W - 36, K.TOP + 318
        y1 = y0 + 24
        d.rectangle((x0, y0, x1, y1), fill=K.CHIP_FILL)
        n = int(np.searchsorted(p["t"], drive_s, side="right"))
        shares = [float((p["cum"][1:n + 1] - p["cum"][:n])[p["band"][:n] == b].sum()) / p["total"]
                  for b in range(len(RAMP))]
        x, w = float(x0), x1 - x0
        segs = [(b, s * w) for b, s in enumerate(shares) if s * w >= 1]
        for i, (b, sw) in enumerate(segs):
            r = (x, y0, max(x, x + sw - (2 if i < len(segs) - 1 else 0)), y1)   # a 1-2 px sliver: no gap
            if i == len(segs) - 1 and sw > 8:
                d.rounded_rectangle(r, 4, fill=RAMP[b], corners=(False, True, True, False))
            else:
                d.rectangle(r, fill=RAMP[b])
            x += sw
        ly = y1 + 22
        sw_w = (x1 - x0) / len(RAMP)
        for b, lab in enumerate(BAND_LABELS):
            lx = x0 + b * sw_w
            d.rounded_rectangle((lx, ly - 7, lx + 14, ly + 7), 3, fill=RAMP[b])
            d.text((lx + 22, ly), lab, font=K.F("small"), fill=K.MUTED, anchor="lm")

    def hud(self, im: Image.Image, i: int, held: str | None) -> None:
        sec = self.secs[i]
        d = ImageDraw.Draw(im)
        card_a = K.smoothstep((sec - CARD_IN) / 0.8)
        K.header(d, "Your nearest shore", "Each road is colored by the shore you'd reach first by car")
        drive_s = self.drive[i] if sec >= RACE_START else 0.0
        p = self.d["people"]
        reached = p["cum"][int(np.searchsorted(p["t"], drive_s, side="right"))] / p["total"]
        if card_a < 1:
            K.stats(d, [(hm(drive_s), "drive time", True), (pct_down(reached), "of people reached", False),
                        ("4", "shores", False)])
            self.people_bar(d, drive_s)
        K.watermark(d, 1 - card_a)

        def label(kicker: str, line: str, place: str, al: float) -> None:
            if al > 0:
                layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
                K.now_label(ImageDraw.Draw(layer), kicker, line, place)
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * al)))
                im.alpha_composite(layer)

        if held in self.d["region"] and RACE_START <= sec <= PULL:
            a, b = held_range(held)
            al = min(1.0, (sec - a) / 0.4, (b - sec) / 0.4)
            top, pct, second, pct2 = self.d["region"][held]
            label(KICKERS.get(held, "The " + held), f"{pct:.0f}% of people here are closest to {SHORE_TEXT[top]}",
                  f"next: {SHORE_TEXT[second]}, {pct2:.0f}%" if pct2 >= 1 else "nobody here is closer to another shore", al)
        if self.upto[i] >= len(self.d["ts"]) and sec <= PULL + 0.3:      # the last road: a callout where it is
            j = self.d["last"]
            (vx, vy, vk), _ = self.camera(sec)
            x = (self.d["mx"][j] - vx) / vk + W / 2
            y = (vy - self.d["my"][j]) / vk + K.MAP_CENTER_Y
            al = min(1.0, (sec - REACHED) / 0.2, (PULL + 0.3 - sec) / 0.3)
            over = Image.new("RGBA", im.size, (0, 0, 0, 0))
            K.callout(ImageDraw.Draw(over), x, y, f"Farthest from any shore: {hm_long(self.d['ts'][-1])}", al)
            im.alpha_composite(over)
        if held == "Last" and sec <= PULL:                 # Denver's three-way tie, then the farthest road
            a = SHOTS[-1][0]
            if sec < REACHED - 0.2:
                (t1, s1), (t2, s2), (t3, s3) = self.d["denver"][:3]
                label("Denver", f"{s1} {hm(t1)} · {s2} {hm(t2)} · {s3} {hm(t3)}",
                      f"three shores within {hm_long(t3 - t1)} of each other",
                      min(1.0, (sec - a) / 0.4, (REACHED - 0.2 - sec) / 0.3))
            else:
                label("The farthest road from any shore", self.d["place"].removeprefix("near "),
                      f"{hm_long(self.d['ts'][-1])} to the nearest one", min(1.0, (sec - REACHED + 0.2) / 0.3, (PULL - sec) / 0.4))
        if held == "final":
            a = PULL + K.PULL_OUT
            top = max(self.d["share"], key=self.d["share"].get)
            label("Every road, its nearest shore", f"{pct_down(self.d['share'][top])} of Americans are closest to "
                  f"{SHORE_TEXT[top]}", "Which shore is yours?", min(1.0, (sec - a) / 0.4, (CARD_IN - sec) / 0.4))
        hook_a = 1 - K.smoothstep((sec - (K.HOOK - 0.3)) / 0.5)
        if hook_a > 0:
            K.hook(im, HOOK_LINES(self.d), HOOK_SUB(self.d), hook_a)
        self.end_card(im, card_a)

    def end_card(self, im: Image.Image, alpha: float) -> int:
        """KPI rows, then the share of people by nearest shore: one bar per shore in its map colour
        (identity, so the colour follows the shore), value at the tip, no grid."""
        dn = self.d["denver"]
        rows = [(hm_long(self.d["ts"][-1]), "farthest road from any shore: " + self.d["place"].removeprefix("near ")),
                (hm_long(dn[2][0] - dn[0][0]), f"Denver's spread: {dn[0][1]}, {dn[1][1]}, {dn[2][1]}"),
                (f"{len(self.d['hx']):,}", "drivable shore points, one every 5 km")]
        top = K.TOP + 160
        bars_top = top + 40 + len(rows) * 100 + 20
        footer = ["Drive time to the nearest drivable shore, no traffic",
                  "Shores: Natural Earth   People: Census 2020   OSM   OSRM"]
        bottom = bars_top + 60 + 4 * 44 + 30 + 36 * (len(footer) + 1) + 30 + 50
        if alpha <= 0:
            return bottom
        layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((50, top, W - 50, bottom), 28, fill=K.CARD_FILL, outline=K.BORDER, width=2)
        for r, (big, label) in enumerate(rows):
            y = top + 40 + r * 100
            d.text((96, y), big, font=K.F("card"), fill=K.INK)
            d.text((W - 96, y + 34), label, font=K.F("small"), fill=K.MUTED, anchor="rm")
        d.text((96, bars_top), "Americans by nearest shore", font=K.F("label"), fill=K.INK)
        ranked = sorted(self.d["share"].items(), key=lambda kv: -kv[1])
        bx0, bx1 = 300, W - 170
        scale = (bx1 - bx0) / ranked[0][1]
        for r, (name, v) in enumerate(ranked):
            y = bars_top + 60 + r * 44
            d.text((bx0 - 20, y + 12), name, font=K.F("label"), fill=K.MUTED, anchor="rm")
            d.rounded_rectangle((bx0, y, bx0 + max(8, v * scale), y + 24), 4, fill=RAMP[SHORES.index(name)],
                                corners=(False, True, True, False))
            d.text((bx0 + v * scale + 14, y + 12), pct_down(v), font=K.F("label"), fill=K.INK, anchor="lm")
        y = bars_top + 60 + 4 * 44 + 30
        for line in footer:
            d.text((W / 2, y), line, font=K.F("small"), fill=K.MUTED, anchor="mt")
            y += 36
        d.text((W / 2, y + 4), f"{K.SERIES}  ·  {K.HANDLE}", font=K.F("label"), fill=K.INK, anchor="mt")
        d.text((W / 2, y + 54), "Which shore is yours?", font=K.F("label"), fill=K.ACCENT, anchor="mt")
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
        im.alpha_composite(layer)
        return bottom

    def frame(self, i: int) -> bytes:
        sec = self.secs[i]
        view, held = self.camera(sec)
        if held == "hook":
            if self.hook_img is None:
                self.hook_img = self.roads(view, len(self.d["ts"]))
            img = self.hook_img
        elif view[2] > 0.5 * self.us_k:          # any wide shot: resample one 2x US map, drawn incrementally
            upto = int(self.upto[i])               # (redrawing millions of pieces per moving frame took 5-10 s)
            if self.final is None or upto < self.final_done:
                ux, uy, uk = self.view_of("US")
                self.final = Canvas((0, 0, 1, 1), 2 * W, 2 * H)
                self.final.s, self.final.ox, self.final.oy = 2 / uk, W - 2 * ux / uk, 2 * K.MAP_CENTER_Y + 2 * uy / uk
                self.final_done = 0
            self.final.add(self.d["pc"], self.d["order"][self.final_done:upto], self.d["t_fade"], 2, FADE)
            self.final_done = upto
            (ux, uy, uk), (x, y, k) = self.view_of("US"), view
            sc = uk / (2 * k)
            m = np.float32([[sc, 0, W / 2 - uk / k * W / 2 + (ux - x) / k],
                            [0, sc, K.MAP_CENTER_Y - uk / k * K.MAP_CENTER_Y + (y - uy) / k]])
            img = self.dots(cv2.warpAffine(self.final.img[:, :, ::-1], m, (W, H), flags=cv2.INTER_AREA), view)
        else:
            img = self.dots(self.roads(view, int(self.upto[i])), view)
        fa = 1 - (sec - K.HOOK) / FADE_S                            # the hook's finished map fades out
        if held != "hook" and 0 < fa <= 1 and self.hook_img is not None:
            img = (img * (1 - fa) + self.hook_img * fa).astype(np.uint8)
        im = Image.fromarray(img).convert("RGBA")
        im.alpha_composite(self.shade)
        self.hud(im, i, held)
        return im.convert("RGB").tobytes()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["tiktok", "reels"], default="tiktok")
    ap.add_argument("--stills", help="comma-separated seconds; writes PNGs only")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    reel = Reel(args.platform)
    d = reel.d
    print(f"ready in {time.time() - t0:.0f} s; shares {d['share']}; denver {d['denver']}; "
          f"last road {hm_long(d['ts'][-1])} {d['place']}; regions {d['region']}", flush=True)
    if args.stills:
        for sec in map(float, args.stills.split(",")):
            reel.cv = None
            i = min(int(sec * FPS), SECONDS * FPS - 1)
            Image.frombytes("RGB", (W, H), reel.frame(i)).save(OUT / f"reel_{args.platform}_{sec:04.1f}.png")
        print("stills written", flush=True)
        return
    out = OUT / f"coasts_{args.platform}.mp4"
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
