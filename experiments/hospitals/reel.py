"""Every hospital: drive time to the nearest ER, the 65 s social cut (TikTok and Reels).

Every ER "leaves" at once; each road lights up when the nearest one reaches it,
in one blue ramp by drive time (dataviz ordinal ramp, validated on the dark map:
the farthest roads are the brightest, so the ER deserts are what glows). The
camera tours regions as the country fills, then ends way in on the road farthest
from any ER. House layout from experiments/reel_kit.py (Optimal Drives).

    python experiments/hospitals/reel.py --platform tiktok --stills 1,6,20,34,50,62
    python experiments/hospitals/reel.py --platform tiktok
    python experiments/hospitals/reel.py --platform reels

Writes out/hospitals_<platform>.mp4 (silent; add audio in the app).

Every number on screen is from cache/: stores.csv (inventory.py), drivetime.npz
(drivetime.py --stores), CenPop2020_Mean_TR.txt (Census 2020 tract population
centres; each tract takes the drive time of its nearest major-road point).
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
from draw import Canvas, albers, pieces  # noqa: E402

K.WATER = (0, 0, 0)                     # black map: the canvas the roads are drawn on
FPS, W, H = K.FPS, K.W, K.H
SECONDS = 65
RACE_START = K.HOOK + K.DIVE
CARD_IN = SECONDS - K.END_CARD - 0.8
RACE_END = CARD_IN - K.PULL_OUT
CLOCK_SHARE = 0.7                       # 92% of roads are under 30 min: pace mostly by the clock so amber
                                        # (~0:23) and red (~0:28) arrive during the regional tour
LAST_HOLD = 3.0                         # seconds on the farthest road after it's reached
FADE_S = 0.5

# Drive-time bands (minutes) and their colours, so a road's colour changes as the clock passes 0:30 and
# 1:00 (Max, 2026-10-15: "30 and 1 hr both on the same map"). Semantic heat, blue -> amber -> red
# (dataviz: the one multi-hue ordered exception, always with a legend); validated all-pairs on black:
# CVD dE >= 23.8, normal-vision >= 28.4.
BANDS = [30, 60]
BAND_LABELS = ["under 30 min", "30–60 min", "1 hour+"]
RAMP = [(0x2A, 0x78, 0xD6), (0xFA, 0xB2, 0x19), (0xD0, 0x3B, 0x3B)]
DOT = (236, 239, 244)

VIEWS = {"Northeast": (-74.5, 41.6, 700), "Appalachia": (-81.6, 37.6, 600), "Delta": (-90.6, 33.2, 600),
         "Great Plains": (-100.5, 44.0, 1100), "Mountain West": (-113.5, 40.8, 1200), "US": None}
# (second, view): hold where two keys name the same view, move between. "Last" = the farthest road.
# Max (2026-10-15): tour regions as the country fills, then the final road.
SHOTS = [(RACE_START, "Northeast"), (7.5, "Northeast"), (9.2, "Appalachia"), (12.5, "Appalachia"),
         (14.2, "Delta"), (17.5, "Delta"), (19.6, "Great Plains"), (23.5, "Great Plains"),
         (25.6, "Mountain West"), (29.5, "Mountain West"), (32.0, "US"), (37.0, "US"), (40.0, "Last")]
LAST_KM = 180                            # frame width on the farthest road
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
    sf = shapefile.Reader(str(HERE.parents[1] / "data/naturalearth/ne_10m_admin_1_states_provinces"
                              / "ne_10m_admin_1_states_provinces.shp"))
    abroad = shapely.union_all([shape(s.__geo_interface__) for s, r in zip(sf.shapes(), sf.records())
                                if r["adm0_a3"] in ("MEX", "CAN")])
    shapely.prepare(abroad)
    home = ~shapely.contains_xy(abroad, lon.astype(np.float64), lat.astype(np.float64))

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


def prepare() -> dict:
    r, d = np.load(TURF / "cache" / "roads.npz"), np.load(CACHE / "drivetime.npz")
    arrival = np.where(road_ok(r["lon"], r["lat"], d["arrival_s"]), d["arrival_s"], np.nan).astype(np.float32)
    data = dict(lon=r["lon"], lat=r["lat"], start=r["start"], winner=d["winner"], arrival=arrival)
    pc = pieces(data)
    pc["chain"] = band(pc["t"])                       # colour index = band (draw.COLOURS is the ramp)
    order = np.argsort(pc["t"], kind="stable")
    ts = pc["t"][order]
    mx, my = ((pc["p0"] + pc["p1"]) / 2).T
    last = int(order[-1])

    # people: each tract centre takes the drive time of its nearest reached road point
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
    pop, st = np.array(pop, np.float64), np.array(st)
    by_t = np.argsort(pt)
    people = dict(t=pt[by_t], cum=np.concatenate([[0.0], np.cumsum(pop[by_t])]), band=band(pt[by_t]),
                  total=float(pop.sum()))
    b = band(pt)
    # states ranked by share of people 30+ min out; (share 30-60, share 60+, state)
    states = sorted(((float(pop[(st == s) & (b == 1)].sum() / pop[st == s].sum()),
                      float(pop[(st == s) & (b == 2)].sum() / pop[st == s].sum()), s) for s in set(st)),
                    key=lambda r: r[0] + r[1], reverse=True)

    region = {}
    hx, hy = hospital_points()
    for name, v in VIEWS.items():
        if v:
            x, y = albers([v[0]], [v[1]])
            m = (np.abs(px - x[0]) < v[2] / 2) & (np.abs(py - y[0]) < v[2] / 2)
            n_h = int(((np.abs(hx - x[0]) < v[2] / 2) & (np.abs(hy - y[0]) < v[2] / 2)).sum())
            region[name] = (round(100 * pop[m & (b >= 1)].sum() / pop[m].sum()),
                            round(100 * pop[m & (b == 2)].sum() / pop[m].sum()), n_h)
    lon_l, lat_l = lonlat_of(mx[last], my[last])
    return dict(pc=pc, order=order, ts=ts, mx=mx, my=my, last=last, hx=hx, hy=hy, people=people,
                far30=float(pop[b >= 1].sum()), far60=float(pop[b == 2].sum()),
                states=states, region=region, place=place_name(lon_l, lat_l),
                t_fade=float(np.percentile(ts, 99.5)))


def hospital_points():
    lon, lat = [], []
    with open(CACHE / "stores.csv", encoding="utf-8") as fh:
        for s in csv.DictReader(fh):
            lon.append(float(s["lon"]))
            lat.append(float(s["lat"]))
    return albers(lon, lat)


def lonlat_of(x: float, y: float) -> tuple[float, float]:
    """Inverse of albers() by a small grid search, plenty for a place name."""
    lon, lat = np.meshgrid(np.linspace(-125, -66, 1200), np.linspace(24, 50, 600))
    ax, ay = albers(lon, lat)
    i = np.argmin((ax - x) ** 2 + (ay - y) ** 2)
    return float(lon.flat[i]), float(lat.flat[i])


def place_name(lon: float, lat: float) -> str:
    """'near Town, ST' from Nominatim, cached."""
    path = CACHE / "places.json"
    cache = json.load(open(path)) if path.exists() else {}
    key = f"{lon:.3f},{lat:.3f}"
    if key not in cache:
        q = urllib.parse.urlencode(dict(lat=lat, lon=lon, format="json", zoom=10, addressdetails=1))
        req = urllib.request.Request(f"https://nominatim.openstreetmap.org/reverse?{q}",
                                     headers={"User-Agent": "my-osrm hospitals video (mapzimus)"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            a = json.load(resp).get("address", {})
        town = a.get("city") or a.get("town") or a.get("village") or a.get("hamlet") or a.get("county", "")
        cache[key] = f"near {town}, {a.get('state', '')}".strip(", ")
        json.dump(cache, open(path, "w"), indent=0)
    if re.match(r"near T\d+ R\d+", cache[key]):          # Maine's unorganized townships: "T8 R16 WELS"
        return "North Maine Woods"
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


def close_up(view) -> int:
    return 2 if view[2] < 0.35 else 1


def held_range(name: str) -> tuple[float, float]:
    for (s0, v0), (s1, v1) in zip(SHOTS, SHOTS[1:]):
        if v0 == v1 == name:
            return s0, s1
    return SHOTS[-1][0], RACE_END


def hm(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60}:{m % 60:02d}"


def hm_long(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def millions(n: float) -> str:
    return f"{n / 1e6:.1f} million"


# --- frames -----------------------------------------------------------------------

class Reel:
    def __init__(self, platform: str):
        K.use(platform)
        draw.COLOURS[:] = RAMP
        self.d = prepare()
        pc = self.d["pc"]
        n = SECONDS * FPS
        self.secs = np.arange(n) / FPS
        r0, r1 = int(RACE_START * FPS), int((RACE_END - LAST_HOLD) * FPS)
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
        self.hook_img, self.final = None, None
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
        if sec > RACE_END:
            return move(self.view_of(SHOTS[-1][1]), self.card_view, min(1.0, (sec - RACE_END) / K.PULL_OUT)), None
        for (s0, v0), (s1, v1) in zip(SHOTS, SHOTS[1:]):
            if sec <= s1:
                if v0 == v1:
                    return self.view_of(v0), v0
                return move(self.view_of(v0), self.view_of(v1), (sec - s0) / (s1 - s0)), None
        return self.view_of(SHOTS[-1][1]), SHOTS[-1][1]

    def dots(self, img: np.ndarray, view) -> np.ndarray:
        """Every ER as a small white dot: the single series, named in the title."""
        x, y, k = view
        px = (self.d["hx"] - x) / k + W / 2
        py = (y - self.d["hy"]) / k + K.MAP_CENTER_Y
        m = (px > -5) & (px < W + 5) & (py > -5) & (py < H + 5)
        zoom = math.log2(max(self.us_k / k, 1.0))
        r = float(np.clip(0.9 + 0.9 * zoom, 0.9, 5.0))
        col = tuple(int(c * min(1.0, 0.55 + 0.25 * zoom)) for c in DOT)     # dim at US scale: the roads are the story
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
            self.cv.add(pc, self.in_view(order[:upto], view), tf, close_up(view), 1.0)
        else:
            self.cv.add(pc, self.in_view(order[self.done:upto], view), tf, close_up(view), 1.0)
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
            r = (x, y0, x + sw - (2 if i < len(segs) - 1 else 0), y1)
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
        K.header(d, "Every ER in the lower 48", "Each road is colored by the drive to the nearest ER")
        drive_s = self.drive[i] if sec >= RACE_START else 0.0
        p = self.d["people"]
        reached = p["cum"][int(np.searchsorted(p["t"], drive_s, side="right"))] / p["total"]
        if card_a < 1:
            K.stats(d, [(hm(drive_s), "drive time", True), (f"{round(100 * reached)}%", "of people reached", False),
                        (f"{len(self.d['hx']):,}", "hospitals with an ER", False)])
            self.people_bar(d, drive_s)
        K.watermark(d, 1 - card_a)
        if held in self.d["region"] and RACE_START <= sec <= RACE_END:
            a, b = held_range(held)
            al = min(1.0, (sec - a) / 0.4, (b - sec) / 0.4)
            if al > 0:
                p30, p60, n_h = self.d["region"][held]
                layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
                K.now_label(ImageDraw.Draw(layer), "The " + held if held != "Appalachia" else held,
                            f"{p30}% of people live 30+ min from an ER" + (f" · {p60}% an hour+" if p60 else ""),
                            f"{n_h:,} hospitals with an ER in view")
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * al)))
                im.alpha_composite(layer)
        if self.upto[i] >= len(self.d["ts"]) and sec <= CARD_IN:      # the last road: a callout where it is
            j = self.d["last"]
            (vx, vy, vk), _ = self.camera(sec)
            x = (self.d["mx"][j] - vx) / vk + W / 2
            y = (vy - self.d["my"][j]) / vk + K.MAP_CENTER_Y
            al = min(1.0, (sec - self.secs[np.argmax(self.upto >= len(self.d["ts"]))]) / 0.2)
            over = Image.new("RGBA", im.size, (0, 0, 0, 0))
            K.callout(ImageDraw.Draw(over), x, y, f"Farthest from an ER: {hm_long(self.d['ts'][-1])}", al)
            im.alpha_composite(over)
        if held == "Last" and sec <= RACE_END:                     # the whole final hold, not just once reached
            al = min(1.0, (sec - SHOTS[-1][0]) / 0.4, (RACE_END - sec) / 0.4)
            if al > 0:
                lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
                K.now_label(ImageDraw.Draw(lay), "The last road to reach", self.d["place"],
                            "private logging roads, gravel most of the way"
                            if self.d["place"] == "North Maine Woods" else "the longest drive to an ER")
                lay.putalpha(lay.getchannel("A").point(lambda v: int(v * al)))
                im.alpha_composite(lay)
        hook_a = 1 - K.smoothstep((sec - (K.HOOK - 0.3)) / 0.5)
        if hook_a > 0:
            K.hook(im, [millions(self.d["far30"]), "Americans"],
                   f"live 30+ min from an ER · {millions(self.d['far60'])} an hour+", hook_a)
        self.end_card(im, card_a)

    def end_card(self, im: Image.Image, alpha: float) -> int:
        """KPI rows, then the five states with the largest share of people 30+ min from an ER: ranked
        stacked bars (30-60 min, then 1 hour+, 2px gap, the map's colours), total at the tip, no grid."""
        rows = [(f"{len(self.d['hx']):,}", "hospitals with an ER open to everyone"),
                (millions(self.d["far30"]), "people 30+ min from one"),
                (millions(self.d["far60"]), "people an hour+ from one"),
                (hm_long(self.d["ts"][-1]), "longest drive: " + self.d["place"].removeprefix("near ")
                 + (" logging roads" if self.d["place"] == "North Maine Woods" else ""))]
        top = K.TOP + 160
        bars_top = top + 40 + len(rows) * 100 + 20
        footer = ["Drive time from the nearest ER, no traffic",
                  "Hospitals: CMS (2026)   People: Census 2020   Roads: OSM   Routing: OSRM"]
        bottom = bars_top + 88 + 5 * 44 + 30 + 36 * (len(footer) + 1) + 30
        if alpha <= 0:
            return bottom
        layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((50, top, W - 50, bottom), 28, fill=K.CARD_FILL, outline=K.BORDER, width=2)
        for r, (big, label) in enumerate(rows):
            y = top + 40 + r * 100
            d.text((96, y), big, font=K.F("card"), fill=K.INK)
            d.text((W - 96, y + 34), label, font=K.F("small"), fill=K.MUTED, anchor="rm")
        d.text((96, bars_top), "Share of people 30+ min from an ER", font=K.F("label"), fill=K.INK)
        lx = 96
        for c in (1, 2):                                                    # legend row: the two stacked parts
            d.rounded_rectangle((lx, bars_top + 50, lx + 14, bars_top + 64), 3, fill=RAMP[c])
            d.text((lx + 22, bars_top + 57), BAND_LABELS[c], font=K.F("small"), fill=K.MUTED, anchor="lm")
            lx += 22 + K.F("small").getlength(BAND_LABELS[c]) + 36
        top5 = self.d["states"][:5]
        bx0, bx1 = 170, W - 190
        scale = (bx1 - bx0) / (top5[0][0] + top5[0][1])
        for r, (s30, s60, st) in enumerate(top5):
            y = bars_top + 88 + r * 44 + 10
            d.text((bx0 - 20, y + 12), st, font=K.F("label"), fill=K.MUTED, anchor="rm")
            w1, w2 = s30 * scale, s60 * scale
            d.rectangle((bx0, y, bx0 + max(1, w1 - (2 if w2 >= 1 else 0)), y + 24), fill=RAMP[1])
            if w2 >= 1:
                d.rounded_rectangle((bx0 + w1, y, bx0 + w1 + max(w2, 8), y + 24), 4, fill=RAMP[2],
                                    corners=(False, True, True, False))
            d.text((bx0 + w1 + w2 + 14, y + 12), f"{round(100 * (s30 + s60))}%", font=K.F("label"),
                   fill=K.INK, anchor="lm")
        y = bars_top + 88 + 5 * 44 + 30
        for line in footer:
            d.text((W / 2, y), line, font=K.F("small"), fill=K.MUTED, anchor="mt")
            y += 36
        d.text((W / 2, y + 4), f"{K.SERIES}  ·  {K.HANDLE}", font=K.F("label"), fill=K.INK, anchor="mt")
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
        elif sec > RACE_END and view[2] > 0.5 * self.us_k:           # pull-out, wide: resample the finished map
            if self.final is None:
                ux, uy, uk = self.view_of("US")
                big = Canvas((0, 0, 1, 1), 2 * W, 2 * H)
                big.s, big.ox, big.oy = 2 / uk, W - 2 * ux / uk, 2 * K.MAP_CENTER_Y + 2 * uy / uk
                big.add(self.d["pc"], self.d["order"], self.d["t_fade"], 2, 1.0)
                self.final = big.img[:, :, ::-1].copy()
            (ux, uy, uk), (x, y, k) = self.view_of("US"), view
            sc = uk / (2 * k)
            m = np.float32([[sc, 0, W / 2 - uk / k * W / 2 + (ux - x) / k],
                            [0, sc, K.MAP_CENTER_Y - uk / k * K.MAP_CENTER_Y + (y - uy) / k]])
            img = self.dots(cv2.warpAffine(self.final, m, (W, H), flags=cv2.INTER_AREA), view)
        else:
            img = self.dots(self.roads(view, int(self.upto[i])), view)
            fa = 1 - (sec - K.HOOK) / FADE_S
            if 0 < fa <= 1 and self.hook_img is not None:
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
    print(f"ready in {time.time() - t0:.0f} s; 30+ min {d['far30']:,.0f} people, 1 h+ {d['far60']:,.0f}; "
          f"last road {hm_long(d['ts'][-1])} {d['place']}; regions {d['region']}; "
          f"states {[(s, round(100 * a), round(100 * b)) for a, b, s in d['states'][:8]]}", flush=True)
    if args.stills:
        for sec in map(float, args.stills.split(",")):
            reel.cv = None
            i = min(int(sec * FPS), SECONDS * FPS - 1)
            Image.frombytes("RGB", (W, H), reel.frame(i)).save(OUT / f"reel_{args.platform}_{sec:04.1f}.png")
        print("stills written", flush=True)
        return
    out = OUT / f"hospitals_{args.platform}.mp4"
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
