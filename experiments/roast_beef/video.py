"""Optimal Drives: every roast beef shop in Massachusetts (North Shore roast
beef country and beyond). A 65 s reel per platform, adapted from
walmart/video.py: still shots that frame the route, a slowed-down pass through
the North Shore, callouts for the shops readers rank highest.

House style comes from experiments/reel_kit.py (spec: docs/optimal-drives-format.md).
Run inventory.py, then experiments/walmart/build.py --cache experiments/roast_beef/cache.

    python experiments/roast_beef/video.py --platform tiktok --stills 1,15,30,45,63
    experiments/render.ps1 roast_beef -Platform tiktok
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import subprocess
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import cv2
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
CLIP = (-74.5, 40.5, -69.0, 43.6)                # New England, around Massachusetts
SANDWICH_MIN = 15                      # end card: a stop for a sandwich at every shop
FLIGHT_STOPS, HOP_STOPS = 150, 70      # pseudo-stops: long flights ~1 s on screen, island hops less
BLUE = (255, 176, 59)                  # a shop once reached: the house amber (kept the name from walmart/)
# Camera (Max, 2026-10-14: the follow-cam was too zoomed in at the start, the
# national view too far out later, no metro close-ups): a list of shots, each a
# still frame on what the car covers in it, with short glides between them.
SHOT_S = 2.5                           # road shots: about this long on screen
GLIDE_S = 0.7                          # transition between shots
PAD = 0.12                             # margin around a shot's route
MIN_ROAD_KM, MIN_DIVE_KM = 35, 14      # narrowest frame for a road shot / the North Shore dive
# No metro dive (walmart/ has them): most shops are one North Shore run, which took 27 s
# as a single still frame; the 2.5 s road shots already frame its streets tightly.
METROS = {}
DIVE_WEIGHT = 14
DIVE_STOPS = 500                       # pseudo-stops for the dive
# Highlights: (lon, lat) of the shop -> callout; hold=True pauses the clock and zooms in.
# Boston.com readers' poll, 2024 (184 readers): Nick's #1 (34 votes), Bella's #3, Londi's #4,
# Tessi's #5; Kelly's, Revere Beach: "the claimed birthplace of the roast beef sandwich"
# (founded 1951, Wikipedia). The Modern Butcher: no "beef" in the name, still on the list.
HIGHLIGHTS = [((-70.983961, 42.423508), "Kelly's: says it invented the sandwich (1951)", True),
              ((-70.884154, 42.582664), "Readers' #1: Nick's, Beverly", True),
              ((-71.112232, 42.718584), "Readers' #3: Bella's", False),
              ((-71.135296, 42.684008), "Readers' #4: Londi's", False),
              ((-71.271259, 42.646783), "Readers' #5: Tessi's", False),
              ((-70.936355, 42.566282), "The Modern Butcher", False)]
HOLD_S = 1.6
HOLD_KM = 5                            # half-width of a highlight's zoom (walmart/: 50)
CALLOUT_S = 1.8

GROUPS = ["West", "North", "North Shore", "Boston", "South"]
CHIP = {"West": "WEST", "North": "NORTH", "North Shore": "SHORE", "Boston": "BOS", "South": "SOUTH"}

# --- projection: Albers equal-area conic on a sphere, km --------------------------

R, LON0, P1, P2, PHI0 = 6371.0, -71.8, 41.3, 42.9, 42.1        # Albers centred on Massachusetts
_n = (math.sin(math.radians(P1)) + math.sin(math.radians(P2))) / 2
_C = math.cos(math.radians(P1)) ** 2 + 2 * _n * math.sin(math.radians(P1))
_rho0 = R * math.sqrt(_C - 2 * _n * math.sin(math.radians(PHI0))) / _n


def km(lon, lat):
    lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    rho = R * np.sqrt(_C - 2 * _n * np.sin(lat)) / _n
    th = _n * (lon - math.radians(LON0))
    return rho * np.sin(th), _rho0 - rho * np.cos(th)


def group(s: dict) -> str:
    """Rough regions for the chips: Boston and the inner suburbs, the North Shore (coastal
    Essex County), the rest north of Route 128 / I-95's latitude, the west, the south."""
    lon, lat = s["lon"], s["lat"]
    if math.hypot((lon + 71.06) * 0.74, lat - 42.36) * 111.2 < 13:
        return "Boston"
    if lat > 42.42 and lon > -71.05:
        return "North Shore"
    if lat > 42.42:
        return "North"
    return "West" if lon < -71.3 else "South"


def ferry_name(name: str) -> str:
    """OSM ferry names, tidied for the label."""
    return name.replace("Linconlville", "Lincolnville").replace(" - ", " – ").replace(" ↔ ", " – ")


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
    st = tour["stops"]
    drive_h = tour["hours"]
    n_ns = sum(group(x) == "North Shore" for x in st)
    real_h = drive_h + len(st) * SANDWICH_MIN / 60
    rows = [(f"{drive_h:.1f} hours", "of driving, nonstop"),
            (f"{tour['miles']:,.0f} miles", f"{st[0]['town']} to {st[-1]['town']}"),
            (f"{len(st)} shops", f"{n_ns} of them on the North Shore"),
            (f"{real_h:.0f} hours", f"with {SANDWICH_MIN} min for a sandwich at each")]
    footer = ["Shops: OpenStreetMap, Boston.com readers' list (2024)",
              "Routing: OSRM, no traffic"]
    return rows, footer


ISLAND_AIRPORTS = {"SJU", "HNL", "OGG", "KOA", "ITO", "LIH"}


def _near(st: list[dict], lon: float, lat: float, r_km: float) -> np.ndarray:
    k = math.cos(math.radians(lat))
    return np.array([j for j, x in enumerate(st)
                     if math.hypot((x["lon"] - lon) * k, x["lat"] - lat) * 111.2 < r_km], int)


def metro_runs(st: list[dict]) -> list[tuple[str, int, int]]:
    """(name, first, last) stop index of each metro's longest run of consecutive stores."""
    out = []
    for name, spec in METROS.items():
        parts = spec if isinstance(spec[0], tuple) else (spec,)     # Central Florida: Tampa + Orlando
        longest = [_longest_run(_near(st, *part)) for part in parts]
        out.append((name, min(r[0] for r in longest), max(r[-1] for r in longest)))
    return sorted(out, key=lambda r: r[1])


def _longest_run(idx: np.ndarray) -> list[int]:
    runs, run = [], [int(idx[0])]
    for a, b in zip(idx, idx[1:]):
        if b == a + 1:
            run.append(int(b))
        else:
            runs.append(run)
            run = [int(b)]
    return max(runs + [run], key=len)


def highlights(st: list[dict]) -> list[tuple[int, str, bool]]:
    """(stop index, callout, hold) for the famous stores and the four extremes."""
    out = []
    for (lon, lat), text, hold in HIGHLIGHTS:
        j = min(range(len(st)), key=lambda j: (st[j]["lon"] - lon) ** 2 + (st[j]["lat"] - lat) ** 2)
        out.append((j, text, hold))
    return out


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

    st = tour["stops"]
    dives = metro_runs(st)
    holds = [j for j, _, hold in highlights(st) if hold]

    # Pacing: flights get FLIGHT_STOPS pseudo-stops so the plane is on screen
    # long enough to read; metro dives get DIVE_WEIGHT x per store. Holds pause
    # the clock on a highlight. None of this is counted, it only shapes the pace.
    pace = list(stop_t)
    for fl in tour["flights"]:
        k = HOP_STOPS if fl["frm"] in ISLAND_AIRPORTS and fl["to"] in ISLAND_AIRPORTS else FLIGHT_STOPS
        pace += list(np.linspace(fl["t0"], fl["t1"], k + 2)[1:-1] / 3600)
    for _, a, b in dives:
        pace += list(np.linspace(stop_t[a], stop_t[b], DIVE_STOPS + 2)[1:-1])
    for j, x in enumerate(st[:-1]):
        if x["region"] in ("HI", "PR") and st[j + 1]["region"] == x["region"]:
            pace += list(np.linspace(stop_t[j], stop_t[j + 1], DIVE_WEIGHT + 1)[1:-1])
    hold_n = int(HOLD_S * FPS)
    core = K.warp(np.sort(pace), total, f1 + 1 - f0 - hold_n * len(holds))
    for j in sorted(holds, key=lambda j: -stop_t[j]):        # back to front: indices stay valid
        k = int(np.searchsorted(core, stop_t[j]))
        core = np.concatenate([core[:k], np.full(hold_n, stop_t[j]), core[k:]])
    drive = np.zeros(n)
    drive[f0:f1 + 1] = core
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

    # Opening and closing views frame the lower 48.
    l48 = st
    allx, ally = km([x["lon"] for x in l48], [x["lat"] for x in l48])
    ox, oy = (allx.min() + allx.max()) / 2, (ally.min() + ally.max()) / 2

    def fit(top: float, bottom: float) -> tuple[float, float]:
        return max(np.ptp(allx) * 1.08, np.ptp(ally) * W / ((bottom - top) * 0.94)), (top + bottom) / 2

    def frame_of(h: float) -> int:                            # first frame at drive time h
        return int(np.clip(np.searchsorted(drive, h - 1e-9), f0, f1))

    def cam_of(xs, ys, min_w: float) -> tuple[float, float, float]:
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        w = max((x1 - x0) * (1 + 2 * PAD), (y1 - y0) * (1 + 2 * PAD) * W / (K.MAP_BOTTOM - K.MAP_TOP), min_w)
        return (x0 + x1) / 2, (y0 + y1) / 2 + (K.MAP_CENTER_Y - H / 2) * w / W, w

    def route_cam(h0: float, h1: float, min_w: float):
        a, b = np.searchsorted(t, h0), np.searchsorted(t, h1, side="right")     # route strictly inside
        xs = np.concatenate([cx[a:b], car_x[frame_of(h0):frame_of(h1) + 1]])
        ys = np.concatenate([cy[a:b], car_y[frame_of(h0):frame_of(h1) + 1]])
        return cam_of(xs, ys, min_w)

    # Special shots (frame ranges): Alaska, flights to and from the islands, each
    # island group, metro dives, highlight holds. Road shots fill the gaps.
    special = []
    fls = tour["flights"]
    isl = lambda c: c in ISLAND_AIRPORTS                      # noqa: E731
    if any(x["state"] == "Alaska" for x in st):              # walmart/: Alaska gets its own shot
        leave_ak = max(x["t"] for x in st if x["state"] == "Alaska") / 3600
        enter_l48 = next(t[v] for v in range(len(t)) if t[v] > leave_ak and
                         coords[v][0] > -125 and coords[v][1] < 49.0)
        special.append((f0, frame_of(enter_l48), route_cam(0, enter_l48, MIN_ROAD_KM)))
    for q, fl in enumerate(fls):
        if isl(fl["frm"]) and isl(fl["to"]):
            continue                                          # inside an island group's shot
        (ax, bx), (ay, by) = km([fl["a"][0], fl["b"][0]], [fl["a"][1], fl["b"][1]])
        lift = 0.25 * math.hypot(bx - ax, by - ay)
        mx, my = (ax + bx) / 2, (ay + by) / 2
        special.append((frame_of(fl["t0"] / 3600), frame_of(fl["t1"] / 3600),
                        cam_of([ax, bx, mx - lift, mx + lift], [ay, by, my - lift, my + lift], MIN_ROAD_KM)))
    for q, fl in enumerate(fls):                              # island group: landing .. next departure off it
        if isl(fl["to"]) and not isl(fl["frm"]):
            out = next(f for f in fls[q + 1:] if isl(f["frm"]) and not isl(f["to"]))
            special.append((frame_of(fl["t1"] / 3600), frame_of(out["t0"] / 3600),
                            route_cam(fl["t1"] / 3600, out["t0"] / 3600, MIN_DIVE_KM)))
    for name, a, b in dives:
        xs, ys = km([x["lon"] for x in st[a:b + 1]], [x["lat"] for x in st[a:b + 1]])
        special.append((frame_of(stop_t[a]), frame_of(stop_t[b]), cam_of(xs, ys, MIN_DIVE_KM)))
    for j in holds:
        (x,), (y,) = km([st[j]["lon"]], [st[j]["lat"]])
        i0 = frame_of(stop_t[j])
        special.append((max(f0, i0 - int(0.6 * FPS)), min(f1, i0 + hold_n + int(0.3 * FPS)),
                        cam_of([x - HOLD_KM, x + HOLD_KM], [y - HOLD_KM, y + HOLD_KM], MIN_DIVE_KM)))
    special.sort()
    shots, i = [], f0

    def fill(i: int, a: int) -> int:
        """Road shots from frame i to a (about SHOT_S each); a tiny gap is left for
        the next shot to start early. Returns where the next shot starts."""
        if a - i > int(0.4 * FPS):
            k = max(1, round((a - i) / (SHOT_S * FPS)))
            for q in range(k):
                e = a if q == k - 1 else i + (a - i) // (k - q)
                shots.append((i, e, route_cam(drive[i], drive[e], MIN_ROAD_KM)))
                i = e
        return i

    for a, b, cam in special:
        a = max(a, i)
        if b <= a:
            continue
        i = fill(i, a)
        shots.append((i, b, cam))                             # starts early over a tiny gap
        i = b
    i = fill(i, f1)
    if i < f1:
        shots[-1] = (shots[-1][0], f1, shots[-1][2])

    # Per-frame camera: hold each shot, glide (smoothstep, log zoom) across each cut.
    xs = np.zeros(n)
    ys = np.zeros(n)
    ws = np.zeros(n)
    for a, b, (x, y, w) in shots:
        xs[a:b + 1], ys[a:b + 1], ws[a:b + 1] = x, y, w
    glide = int(GLIDE_S * FPS)
    lw = np.log(np.maximum(ws, 1))
    sx, sy, slw = xs.copy(), ys.copy(), lw.copy()
    for (a0, b0, c0), (a1, b1, c1) in zip(shots, shots[1:]):
        g = min(glide, (b0 - a0) // 2, (b1 - a1) // 2)
        for i in range(a1 - g, a1 + g + 1):
            k = K.smoothstep((i - (a1 - g)) / max(1, 2 * g))
            sx[i] = c0[0] + (c1[0] - c0[0]) * k
            sy[i] = c0[1] + (c1[1] - c0[1]) * k
            slw[i] = math.log(c0[2]) + (math.log(c1[2]) - math.log(c0[2])) * k

    card_bottom = K.end_card(Image.new("RGBA", (1, 1)), *end_rows(tour), 0)
    nat_w, nat_a = fit(K.MAP_TOP, K.TEXT_FLOOR)
    end_w, end_a = fit(card_bottom + 20, K.TEXT_FLOOR)
    nat = (ox, oy + (nat_a - H / 2) * nat_w / W, nat_w)
    end = (ox, oy + (end_a - H / 2) * end_w / W, end_w)
    first, last = (sx[f0], sy[f0], math.exp(slw[f0])), (sx[f1], sy[f1], math.exp(slw[f1]))

    def blend(c0, c1, k):
        return (c0[0] + (c1[0] - c0[0]) * k, c0[1] + (c1[1] - c0[1]) * k,
                math.exp(math.log(c0[2]) + (math.log(c1[2]) - math.log(c0[2])) * k))

    cams = []
    for i, s in enumerate(secs):
        if s < DRIVE_START:
            cams.append(blend(nat, first, K.smoothstep((s - K.HOOK) / K.DIVE)))
        elif i > f1:
            cams.append(blend(last, end, K.smoothstep((s - DRIVE_END) / K.PULL_OUT)))
        else:
            cams.append((sx[i], sy[i], math.exp(slw[i])))
    return dict(drive=drive, car=(car_x, car_y), cams=cams, idx=np.searchsorted(t, drive),
                miles=np.interp(drive, t, dist), total=total, visit_s=visit_s, land_s=land_s, flying=flying,
                shots=shots, dives=[(nm, frame_of(stop_t[a]), frame_of(stop_t[b])) for nm, a, b in dives])


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
                   for lvl, tol in (("fine", 0.03), ("coarse", 0.6), ("national", 2.0))}
    G["flight_v"] = list(np.flatnonzero(G["mode"] == "flight"))     # route vertex of each flight
    town = {x["name"]: x["town"] for x in tour["stops"]}
    for fl in tour["flights"]:
        fl.setdefault("a_town", town.get(fl["frm"]))      # build.py names the airports
        fl.setdefault("b_town", town.get(fl["to"]))
    st = tour["stops"]
    G["stops"] = np.column_stack(km([s["lon"] for s in st], [s["lat"] for s in st]))
    G["group"] = [group(s) for s in st]
    G["totals"] = {g: G["group"].count(g) for g in GROUPS}
    G["state_n"] = collections.Counter(s["state"] for s in st)
    seen = collections.Counter()
    G["state_done"] = []                  # stop j is the n-th store reached in its state
    for x in st:
        seen[x["state"]] += 1
        G["state_done"].append(seen[x["state"]])
    G["highlights"] = highlights(st)
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

    drive = P["drive"][i]
    k = int(P["idx"][i])
    car = np.array([[P["car"][0][i], P["car"][1][i]]])
    q = int(P["flying"][i])
    plane = None
    if drive > 0:
        route = G["route"]
        lvl = "national" if cw > 3000 else "coarse" if cw > 1500 else "fine"   # national: ~1 px per vertex
        roads = []
        for r, (kind, a, b) in enumerate(G["runs"]):
            if a >= k:
                break
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
            else:
                roads.append(np.round(np.asarray(pts) * 16).astype(np.int32))
        # Roads in one OpenCV pass: PIL's joint="curve" drew a circle per vertex
        # (1.3 M per frame, 12 s); cv2's thick lines have round joins built in.
        if roads:
            arr = np.array(im)
            cv2.polylines(arr, roads, False, K.ACCENT_GLOW, int(SS * 9), cv2.LINE_8, 4)    # SS=2 + resize smooths edges
            cv2.polylines(arr, roads, False, K.ACCENT, int(SS * 3.6), cv2.LINE_8, 4)
            im = Image.fromarray(arr)
            d = ImageDraw.Draw(im)
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

    # Dots: grey until reached, then Walmart blue with a short pop.
    visit_s = P["visit_s"]
    visited = visit_s <= s
    zoom = min(1.5, max(0.7, (400 / cw) ** 0.3))
    stops_px = px(G["stops"])
    for j, ((x, y), v) in enumerate(zip(stops_px, visited)):
        if not (-50 < x < W * SS + 50 and -50 < y < H * SS + 50):
            continue
        if v:
            pop = 1 + 1.6 * max(0.0, 1 - (s - visit_s[j]) / 0.4)
            r, fill = SS * 2.6 * zoom * pop, BLUE
        elif s < DRIVE_START:
            r, fill = SS * 2.2, K.INK
        else:
            r, fill = SS * 2.2 * zoom, K.DOT
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)

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

    # Callouts: highlights for CALLOUT_S after the car gets there; metro names during a dive.
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    live = [(visit_s[j], j, text, hold) for j, text, hold in G["highlights"]
            if 0 <= s - visit_s[j] <= CALLOUT_S + (HOLD_S if hold else 0)]
    if live and s <= DRIVE_END:                              # one at a time: the newest (they bunch up)
        v0, j, text, hold = max(live)
        age, dur = s - v0, CALLOUT_S + (HOLD_S if hold else 0)
        x, y = stops_px[j]
        K.callout(od, x / SS, y / SS, text, min(1.0, age / 0.15, (dur - age) / 0.3))
    for name, a, b in P["dives"]:
        if a <= i <= b:
            al = min(1.0, (i - a) / (0.2 * FPS), (b - i) / (0.3 * FPS))
            box = K.callout_box(W / 2, K.MAP_TOP + 120, name)
            K.callout(od, W / 2, K.MAP_TOP + 120, name, al, box=box)
    im.alpha_composite(over)
    d = ImageDraw.Draw(im)

    n_vis = int(visited.sum())
    n_all = len(tour["stops"])
    card_a = K.smoothstep((s - CARD_IN) / 0.8)
    K.header(d, "Every roast beef shop in Massachusetts", f"The fastest drive to all {n_all}")
    if card_a < 1:
        K.stats(d, [(K.clock(drive), "hours", False), (f"{P['miles'][i]:,.0f}", "miles driven", False),
                    (f"{n_vis}", f"of {n_all} shops", True)])
        done = {g: 0 for g in GROUPS}
        for j in np.flatnonzero(visited):
            done[G["group"][j]] += 1
        K.chips(d, [(CHIP[g], done[g], G["totals"][g]) for g in GROUPS if G["totals"][g]])
    K.watermark(d)

    if DRIVE_START <= s <= DRIVE_END + 0.6:
        if q >= 0:
            fl = tour["flights"][q]
            K.now_label(d, "Flight", f"{fl['a_town']} → {fl['b_town']}",
                        f"{(fl['t1'] - fl['t0']) / 3600:.1f} h incl. 2 h at the airport")
        elif on_ferry:
            fe = next((f for f in tour["ferries"] if f["i0"] < k <= f["i1"]), None)
            mins = round((tour["t"][fe["i1"]] - tour["t"][fe["i0"]]) / 60) if fe else 0
            name = ferry_name(fe["name"]) if fe else "Ferry"
            K.now_label(d, "Ferry", name, f"{mins // 60} h {mins % 60:02d} min with the 30 min boarding wait"
                        if mins >= 60 else f"{mins} min with the 30 min boarding wait")
        elif n_vis:
            j = int(np.flatnonzero(visited).max())
            stp = tour["stops"][j]
            kicker = "Start" if j == 0 else ("Finish" if j == n_all - 1 else f"Shop {j + 1}")
            K.now_label(d, kicker, stp["name"], stp["town"])

    hook_a = 1 - K.smoothstep((s - (K.HOOK - 0.3)) / 0.5)
    K.hook(im, [f"{n_all} shops.", f"{tour['hours']:.1f} hours."],
           "The fastest drive to every roast beef shop in Massachusetts", hook_a, center_y=K.TEXT_FLOOR - 230)
    K.end_card(im, *G["end"], card_a)
    return im.convert("RGB").tobytes()


def sound(tour: dict, path: Path) -> None:
    vs = plan(tour)["visit_s"]
    g = [group(s) for s in tour["stops"]]
    chimes = [max(vs[j] for j in range(len(g)) if g[j] == k) for k in GROUPS if k in g]
    K.soundtrack(path, SECONDS, list(vs[:-1]), chimes, float(vs[-1]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["tiktok", "reels"], default="tiktok")
    ap.add_argument("--stills", help="comma-separated seconds; writes PNGs only")
    # 2 workers: 6 ran the machine out of memory beside the engine (2026-10-13).
    ap.add_argument("--workers", type=int, default=2)
    # Off by default (format doc): add app audio when posting.
    ap.add_argument("--sound", action="store_true", help="ticks per store, chimes, a final chord")
    ap.add_argument("--tour", default=str(CACHE / "tour.json"), help="e.g. cache/sample50/tour.json for a trial")
    # Low memory (2026-10-14, 1.4 GB free): render in parts, each short enough to
    # finish in one sitting, then --join them (stream copy, no re-encode).
    ap.add_argument("--part", help="k/n: render only the k-th of n equal parts (1-based)")
    ap.add_argument("--join", type=int, help="join n rendered parts into the final file")
    args = ap.parse_args()
    tour = json.load(open(args.tour))
    OUT.mkdir(exist_ok=True)
    if args.stills:
        init(tour, args.platform)
        for sec in map(float, args.stills.split(",")):
            i = min(int(sec * FPS), SECONDS * FPS - 1)
            Image.frombytes("RGB", (W, H), render(i)).save(OUT / f"{args.platform}_{sec:04.1f}.png")
        return
    slug = f"roast_beef_{args.platform}"
    silent, wav = OUT / f"{slug}_silent.mp4", OUT / f"{slug}_ticks.wav"
    n, t0 = SECONDS * FPS, time.time()
    if args.join:
        parts = [OUT / f"{slug}_part{k}.mp4" for k in range(1, args.join + 1)]
        lst = OUT / f"{slug}_parts.txt"
        lst.write_text("".join(f"file '{q.name}'\n" for q in parts))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(silent)], check=True)
        lst.unlink()
        for q in parts:
            q.unlink()
    else:
        frames = range(n)
        if args.part:
            k, m = map(int, args.part.split("/"))
            frames = range((k - 1) * n // m, k * n // m)
            silent = OUT / f"{slug}_part{k}.mp4"
        ff = K.encoder(silent)
        if args.workers == 1:                  # in-process: one copy of the map data, not two
            init(tour, args.platform)
            it = map(render, frames)
            pool = None
        else:
            pool = Pool(args.workers, initializer=init, initargs=(tour, args.platform))
            it = pool.imap(render, frames, chunksize=1)
        for i, frame in enumerate(it):
            try:
                ff.stdin.write(frame)
            except OSError:                    # ffmpeg died; say how, not just "broken pipe"
                sys.exit(f"ffmpeg exited with {ff.wait()} at frame {frames[i]}")
            if i % 50 == 0:
                print(f"{i}/{len(frames)} frames, {time.time() - t0:.0f} s", flush=True)
        if pool:
            pool.close()
        ff.stdin.close()
        if ff.wait():
            sys.exit(ff.returncode)
        if args.part:
            print("wrote", silent, f"{time.time() - t0:.0f} s")
            return
    out = OUT / f"{slug}.mp4"
    if args.sound:
        sound(tour, wav)
        K.mux(silent, wav, out)
        silent.unlink()
        wav.unlink()
    else:
        silent.replace(out)
    print("wrote", out, f"{time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
