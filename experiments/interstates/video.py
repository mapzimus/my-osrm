"""Optimal Drives: every 2-digit interstate, end to end, in number order.
A 65 s reel per platform. The whole network shows faint; each interstate
lights up amber as it's driven, connecting drives show dim. Adapted from
amusement_parks/video.py (itself from lighthouses/video.py).

House style comes from experiments/reel_kit.py (spec: docs/optimal-drives-format.md).
Run extract.sh and build.py first.

    python experiments/interstates/video.py --platform tiktok
    python experiments/interstates/video.py --platform reels
    python experiments/interstates/video.py --platform tiktok --stills 0,3,20,40,64
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import shapefile
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter1d
from shapely.geometry import box, shape

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import reel_kit as K  # noqa: E402

ROOT = HERE.parents[1]
CACHE, OUT = HERE / "cache", HERE / "out"
NE_DATA = ROOT / "data" / "naturalearth"

FPS, SS = K.FPS, 2
W, H = K.W, K.H
SECONDS = 65
DRIVE_START = K.HOOK + K.DIVE
DRIVE_END = SECONDS - K.END_CARD - K.PULL_OUT - 0.5
CARD_IN = DRIVE_END + K.PULL_OUT - 0.4
CLIP = (-172.0, 15.0, -60.0, 72.0)
STOP_MIN, SLEEP_H = 240, 8             # real-life trip assumptions (end card): 4 h per park
CALLOUT_S = 1.2
FLIGHT_STOPS = 10                      # a flight paces like this many stops (screen time)

GROUPS = ["2-19", "20-39", "40-59", "60-79", "80-99"]
CONNECT = (92, 84, 66)                 # connecting drives: dim amber
START_DOT, END_DOT = (52, 199, 89), (235, 64, 52)   # until touched, then amber
SHIELD_S = 1.2                         # each interstate's shield pops up where it starts
SHIELD_BLUE, SHIELD_RED = (0, 63, 135), (175, 30, 45)
# Major cities (Max, 2026-10-13): name, lon, lat, label side (r, l, t = above), set so none collide.
CITIES = [("Seattle", -122.33, 47.61, "r"), ("Portland", -122.68, 45.52, "r"), ("San Francisco", -122.42, 37.77, "r"),
          ("Los Angeles", -118.24, 34.05, "r"), ("San Diego", -117.16, 32.72, "l"), ("Las Vegas", -115.14, 36.17, "r"),
          ("Phoenix", -112.07, 33.45, "r"), ("Salt Lake City", -111.89, 40.76, "r"), ("Denver", -104.99, 39.74, "r"),
          ("Dallas", -96.80, 32.78, "r"), ("Houston", -95.37, 29.76, "r"), ("San Antonio", -98.49, 29.42, "l"),
          ("Kansas City", -94.58, 39.10, "l"), ("Minneapolis", -93.27, 44.98, "r"), ("Chicago", -87.63, 41.88, "l"),
          ("St. Louis", -90.20, 38.63, "r"), ("New Orleans", -90.07, 29.95, "r"), ("Atlanta", -84.39, 33.75, "r"),
          ("Miami", -80.19, 25.76, "l"), ("Detroit", -83.05, 42.33, "t"), ("Nashville", -86.78, 36.16, "l"),
          ("Washington", -77.04, 38.91, "r"), ("New York", -74.01, 40.71, "l"), ("Boston", -71.06, 42.36, "l")]


def shield(d: ImageDraw.ImageDraw, x: float, y: float, num: str, alpha: float, size: float) -> None:
    """Interstate shield: red crown, blue body, white number; centred on x, y."""
    a = int(255 * alpha)
    w, h = 1.0 * size, 1.05 * size
    top = y - h / 2
    body = [(x - w / 2, top + 0.22 * h), (x + w / 2, top + 0.22 * h), (x + w / 2, top + 0.55 * h),
            (x + w * 0.3, top + 0.85 * h), (x, top + h), (x - w * 0.3, top + 0.85 * h), (x - w / 2, top + 0.55 * h)]
    crown = [(x - w / 2, top + 0.22 * h), (x - w / 2, top + 0.04 * h), (x - w / 4, top), (x, top + 0.06 * h),
             (x + w / 4, top), (x + w / 2, top + 0.04 * h), (x + w / 2, top + 0.22 * h)]
    outline = [(px_ + (px_ - x) * 0.12, py_ + (py_ - y) * 0.12) for px_, py_ in crown[:-1] + body[2:]]
    d.polygon(outline, fill=(*K.INK, a))
    d.polygon(body, fill=(*SHIELD_BLUE, a))
    d.polygon(crown, fill=(*SHIELD_RED, a))
    f = K.font("segoeuib.ttf", int(size * (0.5 if len(num) < 2 else 0.44)))
    d.text((x, top + 0.58 * h), num, font=f, fill=(*K.INK, a), anchor="mm")


# --- projection: Albers equal-area conic on a sphere, km --------------------------

R, LON0, P1, P2, PHI0 = 6371.0, -100.0, 29.5, 45.5, 37.5
_n = (math.sin(math.radians(P1)) + math.sin(math.radians(P2))) / 2
_C = math.cos(math.radians(P1)) ** 2 + 2 * _n * math.sin(math.radians(P1))
_rho0 = R * math.sqrt(_C - 2 * _n * math.sin(math.radians(PHI0))) / _n


def km(lon, lat):
    lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    rho = R * np.sqrt(_C - 2 * _n * np.sin(lat)) / _n
    th = _n * (lon - math.radians(LON0))
    return rho * np.sin(th), _rho0 - rho * np.cos(th)


def group(n: str) -> str:
    return GROUPS[0] if int(n) < 20 else GROUPS[min(4, int(n) // 20)]


def ferry_name(name: str) -> str:
    """OSM ferry names, tidied for the label."""
    return name.replace("Linconlville", "Lincolnville").replace(" - ", " – ").replace(" ↔ ", " – ")


def famous(name: str) -> None:
    return None


# --- basemap ---------------------------------------------------------------------

def rings(path: Path, tol: float, lines: bool = False) -> list[tuple[np.ndarray, tuple]]:
    """Polygon rings (or lines) in km with their bounding boxes."""
    clip, out = box(*CLIP), []
    for shp in shapefile.Reader(str(path)).iterShapes():
        if shp.shapeType == shapefile.NULL:
            continue
        b = shp.bbox
        if b[0] > CLIP[2] or b[2] < CLIP[0] or b[1] > CLIP[3] or b[3] < CLIP[1]:
            continue
        g = shape(shp.__geo_interface__)
        if not g.intersects(clip):
            continue
        g = g.intersection(clip).simplify(tol)
        for p in getattr(g, "geoms", [g]):
            parts = ([p.exterior, *p.interiors] if p.geom_type == "Polygon"
                     else [p] if p.geom_type == "LineString" else [])
            for r in parts:
                xy = np.asarray(r.coords)
                if len(xy) > 2 or (lines and len(xy) > 1):
                    pk = np.column_stack(km(xy[:, 0], xy[:, 1]))
                    out.append((pk, (*pk.min(0), *pk.max(0))))
    return out


def basemap():
    out = {}
    for lvl, tol in (("fine", 0.006), ("coarse", 0.05)):
        out[lvl] = (rings(NE_DATA / "ne_10m_land" / "ne_10m_land.shp", tol),
                    rings(NE_DATA / "ne_10m_lakes" / "ne_10m_lakes.shp", tol),
                    rings(NE_DATA / "ne_10m_admin_0_countries" / "ne_10m_admin_0_countries.shp", tol),
                    rings(ROOT / "experiments/college_tour/cache/states/ne_10m_admin_1_states_provinces_lines.shp",
                          tol, lines=True))
    return out


# --- timeline -------------------------------------------------------------------

def end_rows(tour: dict) -> tuple[list[tuple[str, str]], list[str]]:
    sg = tour["segs"]
    rows = [(f"{tour['hours']:,.0f} hours", "of driving, nonstop"),
            (f"{tour['miles']:,.0f} miles", f"{tour['interstate_miles']:,.0f} of them on the interstates"),
            (f"{tour['numbers']} interstates", f"I-{sg[0]['num']} to I-{sg[-1]['num']}, in order, {len(sg)} pieces"),
            (f"{tour['hours'] / (24 - SLEEP_H):.0f} days", f"with {SLEEP_H} h sleep a night")]
    footer = [f"Start: {sg[0]['from']}   Finish: {sg[-1]['to']}",
              "Interstates: OpenStreetMap   Routing: OSRM"]
    return rows, footer


def plan(tour: dict):
    n = SECONDS * FPS
    coords = np.asarray(tour["coords"])
    cx, cy = km(coords[:, 0], coords[:, 1])
    t = np.asarray(tour["t"]) / 3600
    dist = np.asarray(tour["dist"]) / 1609.344
    stop_t = np.asarray([s["t"] for s in tour["stops"]]) / 3600
    total = t[-1]
    secs = np.arange(n) / FPS
    f0, f1 = int(DRIVE_START * FPS), int(DRIVE_END * FPS)

    # Pacing: flights get FLIGHT_STOPS pseudo-stops so the plane is on screen
    # long enough to read; they only shape the pace, nothing is counted.
    pace = list(stop_t)
    for fl in tour["flights"]:
        pace += list(np.linspace(fl["t0"], fl["t1"], FLIGHT_STOPS + 2)[1:-1] / 3600)
    drive = np.zeros(n)
    drive[f0:f1 + 1] = K.warp(np.sort(pace), total, f1 + 1 - f0)
    drive[f1 + 1:] = total

    car_x, car_y = np.interp(drive, t, cx), np.interp(drive, t, cy)
    # During a flight the plane flies an arc, not the straight segment.
    flying = np.full(n, -1)
    for q, fl in enumerate(tour["flights"]):
        m = (drive > fl["t0"] / 3600) & (drive < fl["t1"] / 3600)
        flying[m] = q
    visit_s = np.searchsorted(drive[f0:f1 + 1], stop_t - 1e-9) / FPS + DRIVE_START
    land_s = [np.searchsorted(drive[f0:f1 + 1], fl["t1"] / 3600 - 1e-9) / FPS + DRIVE_START
              for fl in tour["flights"]]

    # Follow camera; flights widen it to keep both airports in view.
    look = int(0.6 * FPS)               # tight follow (Max: zoom on the dot as it goes)
    span = np.empty(n)
    for i in range(n):
        a, b = max(0, i - look), min(n, i + look + 1)
        span[i] = max(np.ptp(car_x[a:b]), np.ptp(car_y[a:b]) * W / (K.MAP_BOTTOM - K.MAP_TOP))
    for fl in tour["flights"]:
        (ax, bx), (ay, by) = km([fl["a"][0], fl["b"][0]], [fl["a"][1], fl["b"][1]])
        m = (drive > fl["t0"] / 3600 - 0.5) & (drive < fl["t1"] / 3600 + 0.5)
        span[m] = np.maximum(span[m], max(abs(bx - ax), abs(by - ay) * W / (K.MAP_BOTTOM - K.MAP_TOP)) * 0.75)
    width = np.clip(span * 1.3 + 350, 500, 2600)
    width = np.exp(gaussian_filter1d(np.log(width), FPS * 1.0, mode="nearest"))
    fx = gaussian_filter1d(car_x, FPS * 0.45, mode="nearest")
    fy = gaussian_filter1d(car_y, FPS * 0.45, mode="nearest")

    # Opening and closing views frame the mainland (not Alaska); the islands show up by plane.
    l48 = [s for s in tour["stops"] if s["region"] in ("US", "CA", "MX") and s["lon"] > -140]
    allx, ally = km([s["lon"] for s in l48], [s["lat"] for s in l48])
    ox, oy = (allx.min() + allx.max()) / 2, (ally.min() + ally.max()) / 2

    def fit(top: float, bottom: float) -> tuple[float, float]:
        return max(np.ptp(allx) * 1.08, np.ptp(ally) * W / ((bottom - top) * 0.94)), (top + bottom) / 2

    card_bottom = K.end_card(Image.new("RGBA", (1, 1)), *end_rows(tour), 0)
    intro_w, intro_a = fit(K.MAP_TOP, K.TEXT_FLOOR)
    end_w, end_a = fit(card_bottom + 20, K.TEXT_FLOOR)
    cams = []
    for i, s in enumerate(secs):
        if s < DRIVE_START:
            k, ow, oa = K.smoothstep((s - K.HOOK) / K.DIVE), intro_w, intro_a
        elif s > DRIVE_END:
            k, ow, oa = 1 - K.smoothstep((s - DRIVE_END) / K.PULL_OUT), end_w, end_a
        else:
            k, ow, oa = 1.0, intro_w, intro_a
        j = min(max(i, f0), f1)
        x = ox + (fx[j] - ox) * k
        y = oy + (fy[j] - oy) * k
        w = math.exp(math.log(ow) + (math.log(width[j]) - math.log(ow)) * k)
        a = oa + (K.MAP_CENTER_Y - oa) * k
        cams.append((x, y + (a - H / 2) * w / W, w))
    return dict(drive=drive, car=(car_x, car_y), cams=cams, idx=np.searchsorted(t, drive),
                miles=np.interp(drive, t, dist), total=total, visit_s=visit_s, land_s=land_s, flying=flying)


# --- frame -------------------------------------------------------------------------

G: dict = {}


def init(tour: dict, platform: str) -> None:
    K.use(platform)
    G["tour"] = tour
    G["base"] = basemap()
    G["plan"] = plan(tour)
    c = np.asarray(tour["coords"])
    G["route"] = np.column_stack(km(c[:, 0], c[:, 1]))
    G["mode"] = np.asarray(tour["mode"])
    G["runs"] = split_runs(G["mode"])
    G["simple"] = {lvl: [simplified(G["route"][a:b], tol) for _, a, b in G["runs"]]
                   for lvl, tol in (("fine", 0.03), ("coarse", 0.6))}
    G["flight_v"] = list(np.flatnonzero(G["mode"] == "flight"))     # route vertex of each flight
    town = {x["name"]: x.get("town") for x in tour["stops"]}
    for fl in tour["flights"]:
        fl.setdefault("a_town", town.get(fl["frm"]))      # build.py names the airports
        fl.setdefault("b_town", town.get(fl["to"]))
    st = tour["stops"]
    G["stops"] = np.column_stack(km([s["lon"] for s in st], [s["lat"] for s in st]))
    nums = list(dict.fromkeys(sg["num"] for sg in tour["segs"]))
    G["totals"] = {g: sum(group(n) == g for n in nums) for g in GROUPS}
    G["last_t"] = {n: max(sg["t1"] for sg in tour["segs"] if sg["num"] == n) / 3600 for n in nums}
    G["cities"] = np.column_stack(km([c[1] for c in CITIES], [c[2] for c in CITIES]))
    first = {}
    for j, sg in enumerate(tour["segs"]):
        first.setdefault(sg["num"], 2 * j)          # stop 2j = that piece's start
    G["shields"] = [(j, n) for n, j in first.items()]
    G["net"] = {lvl: [(a, (*a.min(0), *a.max(0))) for a in
                      (simplified(np.column_stack(km(*np.asarray(ln).T)), tol) for ln in tour["network"])]
                for lvl, tol in (("fine", 0.5), ("coarse", 3.0))}
    G["famous"] = {j: f for j, s in enumerate(st) if (f := famous(s["name"]))}
    G["shade"] = K.shade()
    G["end"] = end_rows(tour)


def split_runs(mode: np.ndarray) -> list[tuple[str, int, int]]:
    """(kind, a, b): route vertices a..b-1 travelled by road, ferry or flight.
    mode[i] is how the segment ending at vertex i was travelled."""
    out, a = [], 0
    for i in range(1, len(mode)):
        if i == len(mode) - 1 or mode[i + 1] != mode[i]:
            out.append((str(mode[i]), a, i + 1))
            a = i
    return out


def simplified(xy: np.ndarray, tol: float) -> np.ndarray:
    from shapely.geometry import LineString
    if len(xy) < 3:
        return xy
    return np.asarray(LineString(xy).simplify(tol).coords)


def render(i: int) -> bytes:
    tour, P = G["tour"], G["plan"]
    s = i / FPS
    cx, cy, cw = P["cams"][i]
    scale = W * SS / cw
    ch = cw * H / W
    land, lakes, borders, states = G["base"]["coarse" if cw > 1500 else "fine"]

    def px(xy: np.ndarray) -> list:
        x = (xy[:, 0] - cx) * scale + W * SS / 2
        y = (cy - xy[:, 1]) * scale + H * SS / 2
        return list(zip(x.tolist(), y.tolist()))

    def visible(bb) -> bool:
        return bb[2] > cx - cw / 2 and bb[0] < cx + cw / 2 and bb[3] > cy - ch / 2 and bb[1] < cy + ch / 2

    im = Image.new("RGB", (W * SS, H * SS), K.WATER)
    d = ImageDraw.Draw(im)
    for r, bb in land:
        if visible(bb):
            d.polygon(px(r), fill=K.LAND)
    for r, bb in lakes:
        if visible(bb):
            d.polygon(px(r), fill=K.WATER)
    for r, bb in states + borders:
        if visible(bb):
            d.line(px(r), fill=K.BORDER, width=max(1, int(SS * 1.6)))

    lvl = "coarse" if cw > 1500 else "fine"
    for r, bb in G["net"][lvl]:
        if visible(bb):
            d.line(px(r), fill=K.DOT, width=int(SS * 1.6), joint="curve")

    drive = P["drive"][i]
    k = int(P["idx"][i])
    car = np.array([[P["car"][0][i], P["car"][1][i]]])
    q = int(P["flying"][i])
    plane = None
    if drive > 0:
        route = G["route"]
        lvl = "coarse" if cw > 1500 else "fine"
        # Connecting drives first, interstates on top: a later connector that
        # runs along an already-driven interstate mustn't dim it.
        runs = sorted(enumerate(G["runs"]), key=lambda x: x[1][0] != "connector")
        for r, (kind, a, b) in runs:
            if a >= k:
                continue
            if kind == "flight":
                # Finished flights: a thin grey arc that fades 1.5 s after landing.
                for v in range(a + 1, min(b, k)):
                    fq = G["flight_v"].index(v)
                    age = s - P["land_s"][fq]
                    if 0 <= age < 1.5:
                        c = tuple(int(K.WATER[u] + (K.MUTED[u] - K.WATER[u]) * (1 - age / 1.5)) for u in range(3))
                        at = K.arc(*px(route[v - 1:v + 1]))
                        d.line([at(f / 40)[:2] for f in range(41)], fill=c, width=int(SS * 2))
                continue
            seg = G["simple"][lvl][r] if b <= k else route[a:k]
            if len(seg) < 2:
                continue
            pts = px(seg)
            if kind == "ferry":
                K.dashed(d, pts, K.ACCENT, int(SS * 3.6), SS * 14, SS * 10)
            elif kind == "connector":
                d.line(pts, fill=CONNECT, width=int(SS * 2.2), joint="curve")
            else:
                d.line(pts, fill=K.ACCENT_GLOW, width=int(SS * 9), joint="curve")
                d.line(pts, fill=K.ACCENT, width=int(SS * 3.6), joint="curve")
        # The open segment up to the car (road or ferry); flights draw the plane.
        if q >= 0:
            fl = tour["flights"][q]
            a_px, b_px = px(np.column_stack(km([fl["a"][0], fl["b"][0]], [fl["a"][1], fl["b"][1]])))
            at = K.arc(a_px, b_px)
            f = (drive - fl["t0"] / 3600) / ((fl["t1"] - fl["t0"]) / 3600)
            d.line([at(u / 40 * f)[:2] for u in range(41)], fill=K.MUTED, width=int(SS * 2))
            plane = at(f)
        elif k > 0:
            pts = px(np.vstack([route[k - 1:k], car]))
            if G["mode"][min(k, len(G["mode"]) - 1)] == "ferry":
                K.dashed(d, pts, K.ACCENT, int(SS * 3.6), SS * 14, SS * 10)
            else:
                d.line(pts, fill=K.ACCENT, width=int(SS * 3.6))

    visit_s = P["visit_s"]
    visited = visit_s <= s
    stops_px = px(G["stops"])
    # Every piece: green where it starts, red where it ends, amber once reached.
    for j, ((x, y), v) in enumerate(zip(stops_px, visited)):
        pop = 1 + 1.0 * max(0.0, 1 - (s - visit_s[j]) / 0.4) if v else 1.0
        r = SS * 5.0 * pop
        fill = K.ACCENT if v else (START_DOT if j % 2 == 0 else END_DOT)
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=K.WATER, width=SS)

    # Car, boat or plane.
    on_ferry = q < 0 and 0 < k < len(G["mode"]) and G["mode"][k] == "ferry"
    if K.HOOK <= s <= DRIVE_END + 0.2:
        if plane:
            x, y, ang = plane
            K.glyph(d, K.PLANE, x, y, ang, SS * 2.0)
        elif on_ferry:
            (x, y), = px(car)
            (x0, y0), = px(G["route"][k - 1:k])
            K.glyph(d, K.BOAT, x, y, math.atan2(y - y0, x - x0), SS * 1.6)
        else:
            (x, y), = px(car)
            for r, c in ((SS * 14, K.ACCENT_GLOW), (SS * 8, K.INK)):
                d.ellipse((x - r, y - r, x + r, y + r), fill=c)

    im = im.resize((W, H), Image.LANCZOS).convert("RGBA")
    im.alpha_composite(G["shade"])
    d = ImageDraw.Draw(im)

    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    fc = K.font("segoeui.ttf", 21)
    for (name, _, _, side), (x, y) in zip(CITIES, px(G["cities"])):
        x, y = x / SS, y / SS
        if K.MAP_TOP < y < K.TEXT_FLOOR and 0 < x < W:
            od.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(*K.INK, 230), outline=(*K.WATER, 255), width=2)
            at, anchor = {"r": ((x + 9, y), "lm"), "l": ((x - 9, y), "rm"), "t": ((x, y - 9), "mb")}[side]
            od.text(at, name, font=fc, fill=(*K.INK, 215), anchor=anchor, stroke_width=3, stroke_fill=(*K.WATER, 255))
    for j, n in G["shields"]:
        age = s - visit_s[j]
        if 0 <= age <= SHIELD_S and s <= DRIVE_END:
            a = min(1.0, age / 0.12, (SHIELD_S - age) / 0.3)
            pop = 1 + 0.35 * max(0.0, 1 - age / 0.25)
            x, y = stops_px[j]
            shield(od, x / SS, y / SS - 34, n, a, 46 * pop)
    for j, label in G["famous"].items():
        age = s - visit_s[j]
        if 0 <= age <= CALLOUT_S and s <= DRIVE_END:
            a = min(1.0, age / 0.15, (CALLOUT_S - age) / 0.3)
            x, y = stops_px[j]
            K.callout(od, x / SS, y / SS, label, a)
    im.alpha_composite(over)

    done = [n for n, t1 in G["last_t"].items() if t1 <= drive + 1e-6]
    n_all = len(G["last_t"])
    card_a = K.smoothstep((s - CARD_IN) / 0.8)
    K.header(d, "Every interstate, in order", "Each 2-digit interstate end to end, I-2 to I-99")
    if card_a < 1:
        K.stats(d, [(K.clock(drive), "hours", False), (f"{P['miles'][i]:,.0f}", "miles driven", False),
                    (f"{len(done)}", f"of {n_all} interstates", True)])
        K.chips(d, [(g, sum(group(n) == g for n in done), G["totals"][g]) for g in GROUPS])
    K.watermark(d)

    if DRIVE_START <= s <= DRIVE_END + 0.6:
        segs = tour["segs"]
        cur = next((sg for sg in segs if sg["t0"] / 3600 <= drive <= sg["t1"] / 3600), None)
        if cur:
            K.now_label(d, f"I-{cur['num']} · {cur['dir']}", f"{cur['from']} → {cur['to']}",
                        f"{cur['mi']:,.0f} miles, {cur['h']:.1f} h")
        else:
            nxt = next((sg for sg in segs if sg["t0"] / 3600 > drive), None)
            if nxt:
                K.now_label(d, "Connecting", f"to I-{nxt['num']}", f"starts at {nxt['from']}")

    hook_a = 1 - K.smoothstep((s - (K.HOOK - 0.3)) / 0.5)
    K.hook(im, [f"{n_all} interstates.", f"{tour['hours']:,.0f} hours."],
           "Every 2-digit interstate, end to end, in number order", hook_a, center_y=K.TEXT_FLOOR - 230)
    K.end_card(im, *G["end"], card_a)
    return im.convert("RGB").tobytes()


def sound(tour: dict, path: Path, rate: int = 48000) -> None:
    """Soft tones (Max, 2026-10-13: small audio, not the kit's dings): a low
    pluck when an interstate starts, a higher one when it's finished."""
    import wave
    vs = plan(tour)["visit_s"]
    segs = tour["segs"]
    starts, ends = {}, {}
    for j, sg in enumerate(segs):
        starts.setdefault(sg["num"], vs[2 * j])
        ends[sg["num"]] = vs[2 * j + 1]
    n = int(SECONDS * rate)
    out = np.zeros(n)

    def pluck(t0: float, f: float, amp: float) -> None:
        i = int(t0 * rate)
        m = min(int(0.18 * rate), n - i)
        if m <= 0:
            return
        tt = np.arange(m) / rate
        env = np.exp(-tt / 0.045) * np.minimum(1, tt / 0.004)
        out[i:i + m] += amp * env * (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(4 * np.pi * f * tt))

    for t in starts.values():
        pluck(t, 330.0, 0.5)          # E4
    for t in ends.values():
        pluck(t, 659.3, 0.35)         # E5
    pcm = (out / (np.abs(out).max() or 1) * 0.35 * 32767).astype(np.int16)   # quiet: under app music
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["tiktok", "reels"], default="tiktok")
    ap.add_argument("--stills", help="comma-separated seconds; writes PNGs only")
    # 2 workers: 6 ran the machine out of memory beside the engine (2026-10-13).
    ap.add_argument("--workers", type=int, default=2)
    # On by default here: Max asked for small start/end tones on this one.
    ap.add_argument("--silent", action="store_true", help="no start/end tones")
    args = ap.parse_args()
    tour = json.load(open(CACHE / "tour.json"))
    OUT.mkdir(exist_ok=True)
    if args.stills:
        init(tour, args.platform)
        for sec in map(float, args.stills.split(",")):
            i = min(int(sec * FPS), SECONDS * FPS - 1)
            Image.frombytes("RGB", (W, H), render(i)).save(OUT / f"{args.platform}_{sec:04.1f}.png")
        return
    slug = f"interstates_{args.platform}"
    silent, wav = OUT / f"{slug}_silent.mp4", OUT / f"{slug}_tones.wav"
    ff = K.encoder(silent)
    n, t0 = SECONDS * FPS, time.time()
    with Pool(args.workers, initializer=init, initargs=(tour, args.platform)) as pool:
        for i, frame in enumerate(pool.imap(render, range(n), chunksize=1)):
            ff.stdin.write(frame)
            if i % 300 == 0:
                print(f"{i}/{n} frames, {time.time() - t0:.0f} s", flush=True)
    ff.stdin.close()
    if ff.wait():
        sys.exit(ff.returncode)
    out = OUT / f"{slug}.mp4"
    if not args.silent:
        sound(tour, wav)
        K.mux(silent, wav, out)
        silent.unlink()
        wav.unlink()
    else:
        silent.replace(out)
    print("wrote", out, f"{time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
