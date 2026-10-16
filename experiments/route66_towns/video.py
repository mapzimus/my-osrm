"""Optimal Drives: every town on Route 66, Chicago to Santa Monica (Max, 2026-10-16), in the I-90
video's style (this copies i90_towns/video.py): each town lights up as the drive reaches it, its
dot and level name sized by population; fun-fact callouts at the famous stops (FACTS); New
Mexico drives the 1926-1937 Santa Fe loop, with the 1937 bypass dashed and a callout saying so.
First frame: the US 66 shield and the question; the answer (towns per state) is the end card
(docs/optimal-drives-format.md 6a). Sound: a tick per town, a chime per chip group, a chord.

Run line.py, inventory.py and build.py first.

    python experiments/route66_towns/video.py --platform tiktok --stills 1,10,30,45,63
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

GROUPS = ["IL", "MO-KS", "OK-TX", "NM", "AZ-CA"]           # 5 chips, 52-58 towns each
GROUP_OF = {"IL": "IL", "MO": "MO-KS", "KS": "MO-KS", "OK": "OK-TX", "TX": "OK-TX", "NM": "NM",
            "AZ": "AZ-CA", "CA": "AZ-CA"}
STATE = {"IL": "Illinois", "MO": "Missouri", "KS": "Kansas", "OK": "Oklahoma", "TX": "Texas",
         "NM": "New Mexico", "AZ": "Arizona", "CA": "California"}
ORDER = ["IL", "MO", "KS", "OK", "TX", "NM", "AZ", "CA"]
# Fun facts (town, state) -> callout, shown FACT_S when the drive gets there. Each checked against
# the attraction's own site or the National Park Service Route 66 pages (2026-10-16).
FACTS = {("Chicago", "IL"): "Route 66 begins at Adams St. & Michigan Ave.",
         ("Wilmington", "IL"): "Wilmington: the Gemini Giant, a fiberglass spaceman",
         ("Pontiac", "IL"): "Pontiac: the Route 66 Hall of Fame & Museum",
         ("St. Louis", "MO"): "St. Louis: Ted Drewes custard, on 66 since 1941",
         ("Galena", "KS"): "Kansas gets just 13 miles of Route 66",
         ("Catoosa", "OK"): "Catoosa: the Blue Whale, a 1972 swimming hole",
         ("Clinton", "OK"): "Clinton: the Oklahoma Route 66 Museum",
         ("Adrian", "TX"): "Adrian: the midpoint, 1,139 miles to each end",
         ("Amarillo", "TX"): "Amarillo: Cadillac Ranch, 10 Cadillacs nose-down",
         ("Tucumcari", "NM"): "Tucumcari Tonite! 2,000 motel rooms",
         ("Santa Fe", "NM"): "Until 1937, Route 66 looped through Santa Fe",
         ("Winslow", "AZ"): "Winslow: standin' on the corner",
         ("Holbrook", "AZ"): "Holbrook: sleep in a concrete wigwam",
         ("Seligman", "AZ"): "Seligman: birthplace of Historic Route 66",
         ("Oatman", "AZ"): "Oatman: wild burros walk Main Street",
         ("Santa Monica", "CA"): "End of the Trail. Decommissioned in 1985"}
FACT_S = 2.4
LABEL_S = 1.6           # small towns' labels fade after this; cities keep theirs
KEEP_POP = 250_000      # labels that stay on the map (100k crowded New Jersey)
ROAD_Y = 0.74           # the road sits this far down the map, leaving the sky for names
STATE_S = 1.5           # state-line banner


def size(pop: int) -> tuple[float, float, str]:
    """Dot radius, label px and font for a population (log scale; Max
    2026-10-13: size and boldness by population). Max 2026-10-16: names were too
    small to read on a phone (17-46 px), so 34-72 px and one weight bolder; the
    smallest is about the now-label's size."""
    lg = math.log10(max(pop, 500) / 1000)
    face = ("seguisb.ttf" if pop < 10_000 else "segoeuib.ttf" if pop < 250_000 else "seguibl.ttf")
    return min(11.0, max(3.0, 3.5 + 2.6 * lg)), min(72.0, max(34.0, 32 + 12 * lg)), face


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


def group(state: str) -> str:
    return GROUP_OF[state]


def ferry_name(name: str) -> str:
    """OSM ferry names, tidied for the label."""
    return name.replace("Linconlville", "Lincolnville").replace(" - ", " – ").replace(" ↔ ", " – ")


def famous(name: str, state: str) -> str | None:
    return FACTS.get((name, state))


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
    tw = tour["towns"]
    big = max(tw, key=lambda t: t["pop"])
    n_st = len(tour["states"])
    rows = [(f"{tour['hours']:.1f} hours", "of driving, nonstop"),
            (f"{tour['miles']:,.0f} miles", f"{n_st} states"),
            (f"{len(tw)} towns", f"{sum(t['pop'] for t in tw) / 1e6:.1f} million people live in them"),
            (f"{big['pop'] / 1e6:.2f} million", f"biggest: {clean(big['label'])}, {big['state']}")]
    footer = [f"Start: {tw[0]['label']}, {tw[0]['state']}   Finish: {tw[-1]['label']}, {tw[-1]['state']}",
              "Towns: US Census boundaries 2023, population 2020   Routing: OSRM"]
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
    look = int(0.8 * FPS)               # close follow (Max: zoomed in quite a bit)
    span = np.empty(n)
    for i in range(n):
        a, b = max(0, i - look), min(n, i + look + 1)
        span[i] = max(np.ptp(car_x[a:b]), np.ptp(car_y[a:b]) * W / (K.MAP_BOTTOM - K.MAP_TOP))
    for fl in tour["flights"]:
        (ax, bx), (ay, by) = km([fl["a"][0], fl["b"][0]], [fl["a"][1], fl["b"][1]])
        m = (drive > fl["t0"] / 3600 - 0.5) & (drive < fl["t1"] / 3600 + 0.5)
        span[m] = np.maximum(span[m], max(abs(bx - ax), abs(by - ay) * W / (K.MAP_BOTTOM - K.MAP_TOP)) * 0.75)
    width = np.clip(span * 1.0 + 70, 120, 300)
    width = np.exp(gaussian_filter1d(np.log(width), FPS * 1.0, mode="nearest"))
    fx = gaussian_filter1d(car_x, FPS * 0.45, mode="nearest")
    fy = gaussian_filter1d(car_y, FPS * 0.45, mode="nearest")

    # Opening and closing views frame the mainland (not Alaska); the islands show up by plane.
    l48 = [s for s in tour["stops"] if s["region"] in ("US", "CA", "MX") and s["lon"] > -140]
    allx, ally = km([s["lon"] for s in l48], [s["lat"] for s in l48])
    ox, oy = (allx.min() + allx.max()) / 2, (ally.min() + ally.max()) / 2

    def fit(top: float, bottom: float, pad: float = 1.08) -> tuple[float, float]:
        return max(np.ptp(allx) * pad, np.ptp(ally) * W / ((bottom - top) * 0.94)), (top + bottom) / 2

    card_bottom = answer_card(Image.new("RGBA", (1, 1)), 0, tour)
    # Opening view: the route sits above the hook text, wide enough for
    # Boston's slanted name at the right edge.
    intro_w, intro_a = fit(K.MAP_TOP + 110, K.MAP_TOP + 300, 1.55)   # route under the HUD; shield and question below
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
        a = oa + (K.MAP_TOP + ROAD_Y * (K.MAP_BOTTOM - K.MAP_TOP) - oa) * k
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
    G["towns"] = tour["towns"]
    G["sizes"] = [size(t["pop"]) for t in tour["towns"]]
    G["fonts"] = {(f, int(px_)): K.font(f, int(px_)) for _, px_, f in G["sizes"]}
    G["labels"], G["dims"] = {}, {}
    G["totals"] = {g: sum(group(t["state"]) == g for t in tour["towns"]) for g in GROUPS}
    # First town in each state: where the state-line ding and banner go.
    G["entries"] = [j for j, t in enumerate(tour["towns"]) if j == 0 or tour["towns"][j - 1]["state"] != t["state"]]
    G["famous"] = {j: f for j, t in enumerate(tour["towns"]) if (f := famous(t["name"], t["state"]))}
    ln = json.load(open(CACHE / "line.json", encoding="utf-8"))
    b = np.asarray(ln["bypass"])
    G["bypass"] = np.column_stack(km(b[:, 0], b[:, 1]))
    G["loop_at"] = next(j for j, t in enumerate(tour["towns"]) if t["name"] == "Santa Rosa" and t["state"] == "NM")
    sh = Image.open(HERE / "assets" / "shield.png").convert("RGBA")       # Commons File:US_66_(1926).svg
    sh = sh.crop(sh.getbbox())
    G["shield"] = sh.resize((round(sh.width * 220 / sh.height), 220), Image.LANCZOS)
    pick_sides()
    G["shade"] = K.shade()
    G["end"] = end_rows(tour)


def clean(label: str) -> str:
    """Consolidated city-counties: the city's own name."""
    return {"Butte-Silver Bow (balance)": "Butte"}.get(label, label.replace(" (balance)", ""))


def flat(j: int) -> tuple[Image.Image, int]:
    """Town j's name, level, and the padding around the text in that image."""
    if j not in G["labels"]:
        t = G["towns"][j]
        _, fpx, face = G["sizes"][j]
        f = G["fonts"][face, int(fpx)]
        text, sw, p = clean(t["label"]), max(2, int(fpx / 9)), 8
        l, top, rt, bot = f.getbbox(text, anchor="lt", stroke_width=sw)
        im = Image.new("RGBA", (int(rt - l) + 2 * p, int(bot - top) + 2 * p), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((p - l, p - top), text, font=f, fill=(*K.INK, 255), anchor="lt",
                                stroke_width=sw, stroke_fill=(*K.WATER, 255))
        G["dims"][j] = (rt - l, bot - top)
        G["labels"][j] = im, p
    return G["labels"][j]


def showing(s: float) -> list[int]:
    """Towns whose names may be on screen at s, oldest first."""
    v = G["plan"]["visit_s"]
    return sorted((j for j in np.flatnonzero(v <= s)
                   if s - v[j] < LABEL_S + 0.5 or G["towns"][j]["pop"] >= KEEP_POP), key=lambda j: v[j])


def hits(a, b) -> bool:
    return a[0] < b[2] + 6 and b[0] < a[2] + 6 and a[1] < b[3] and b[1] < a[3]


def pick_sides() -> None:
    """Above or below, fixed per town from the shot where it lights: above,
    unless that hits a name already showing and below is clear. Done once
    up front so every worker agrees and no name ever jumps."""
    P, n = G["plan"], len(G["plan"]["cams"])
    G["side"] = {}
    for j in sorted(range(len(G["towns"])), key=lambda j: P["visit_s"][j]):
        s = P["visit_s"][j]
        if not np.isfinite(s):
            continue
        cx, cy, cw = P["cams"][min(n - 1, max(0, round(s * FPS)))]
        sc = W / cw

        def at(k):
            x, y = G["stops"][k]
            return (x - cx) * sc + W / 2, (cy - y) * sc + H / 2

        flat(j)
        others = []
        for k in showing(s):
            if k != j and k in G["side"]:
                flat(k)
                others.append(label_box(k, *at(k)))
        G["side"][j] = -1
        if any(hits(label_box(j, *at(j)), b) for b in others):
            G["side"][j] = 1
            if any(hits(label_box(j, *at(j)), b) for b in others):
                G["side"][j] = -1


def label_box(j: int, x: float, y: float) -> tuple[float, float, float, float]:
    """Screen box of town j's name, centred over (or under) its dot at x, y."""
    w, h = G["dims"][j]
    gap = G["sizes"][j][0] * 1.3 + 12           # clear of the road line
    top = y - gap - h if G["side"][j] < 0 else y + gap
    return x - w / 2, top, x + w / 2, top + h


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


WIN_RGB, REST_RGB = (0xC4, 0x87, 0x0B), (0x56, 0x5E, 0x6E)     # format doc 6a (validated on the card)


def hook_q(im: Image.Image, alpha: float) -> None:
    """The first frame: the US 66 shield and the question (format doc 6a)."""
    if alpha <= 0:
        return
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    dd = ImageDraw.Draw(layer)
    a = int(255 * alpha)
    lines = ["How many towns", "does Route 66", "run through?"]
    f0 = K.F("hook")
    size = f0.size
    while max(f0.font_variant(size=size).getlength(t) for t in lines) > W - 90:
        size -= 4
    f = f0.font_variant(size=size)
    lh = int(size * 1.12)
    y0 = K.TEXT_FLOOR - 200 - len(lines) * lh / 2
    dd.rectangle((0, y0 - 40, W, y0 + len(lines) * lh + 40), fill=(0, 0, 0, int(170 * alpha)))
    for r, t in enumerate(lines):
        dd.text((W / 2, y0 + r * lh), t, font=f, fill=(*(K.INK if r < 2 else K.ACCENT), a), anchor="mt")
    lg = G["shield"]
    if alpha < 1:
        lg = lg.copy()
        lg.putalpha(lg.getchannel("A").point(lambda v: int(v * alpha)))
    layer.alpha_composite(lg, ((W - lg.width) // 2, int(y0 - 50 - lg.height)))
    im.alpha_composite(layer)


def answer_card(im: Image.Image, alpha: float, tour: dict) -> int:
    """The answer (format doc 6a): towns big, then towns per state as bars in route order, the
    state with the most in gold, the rest grey; a name and value on every bar."""
    towns = tour["towns"]
    per = collections.Counter(t["state"] for t in towns)
    top = K.TOP + 150
    row_h = 54
    bottom = top + 40 + 250 + len(ORDER) * row_h + 30 + 36 * 2 + 50
    if alpha <= 0:
        return bottom
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((40, top, W - 40, bottom), 28, fill=K.CARD_FILL, outline=K.BORDER, width=2)
    y = top + 34
    d.text((W / 2, y), "TOWNS ON ROUTE 66", font=K.F("tag"), fill=K.MUTED, anchor="mt")
    y += 46
    d.text((W / 2, y), f"{len(towns)} towns", font=K.F("hook_sub").font_variant(size=96), fill=K.INK, anchor="mt")
    y += 118
    pop = sum(t["pop"] for t in towns)
    d.text((W / 2, y), f"{pop / 1e6:.1f} million people  ·  {tour['miles']:,.0f} miles  ·  {tour['hours']:.0f} hours",
           font=K.F("small"), fill=K.MUTED, anchor="mt")
    y = top + 40 + 250
    best = max(ORDER, key=lambda c: per[c])
    x0, x1 = 330, W - 170
    for k, c in enumerate(ORDER):
        yy = y + k * row_h
        d.text((x0 - 16, yy + 26), STATE[c], font=K.F("small"), fill=K.INK, anchor="rm")
        bw = max(8, (x1 - x0) * per[c] / per[best])
        d.rounded_rectangle((x0, yy + 11, x0 + bw, yy + 41), 4, fill=WIN_RGB if c == best else REST_RGB)
        d.text((x0 + bw + 14, yy + 26), f"{per[c]}", font=K.F("small"), fill=K.INK, anchor="lm")
    y += len(ORDER) * row_h + 30
    for line in ["New Mexico: the 1926-1937 road through Santa Fe",
                 "Towns: Census 2020   Route: OpenStreetMap   Routing: OSRM"]:
        d.text((W / 2, y), line, font=K.F("small"), fill=K.MUTED, anchor="mt")
        y += 36
    d.text((W / 2, y + 4), f"{K.SERIES}  ·  {K.HANDLE}", font=K.F("label"), fill=K.ACCENT, anchor="mt")
    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    im.alpha_composite(layer)
    return bottom


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
    order = sorted(range(len(G["towns"])), key=lambda j: G["towns"][j]["pop"])   # big towns on top
    hook = s < DRIVE_START
    # Names sit level, centred above their dots, or below when a close
    # neighbour has the space above (Max 2026-10-15: slanted names were
    # unreadable; alternate when two are close). Sides come from pick_sides().
    # If both sides are taken, the newer name knocks the older out, unless
    # the older town is 4x bigger: the big names stay put and the small ones
    # take turns.
    lit = showing(s)
    boxes = {}
    for j in lit:
        flat(j)                                     # fills G["dims"]
        boxes[j] = label_box(j, stops_px[j][0] / SS, stops_px[j][1] / SS)
    gone = {}
    for j in lit:
        for k2 in lit:
            if visit_s[k2] <= visit_s[j] or G["towns"][k2]["pop"] * 4 < G["towns"][j]["pop"]:
                continue
            if hits(boxes[j], boxes[k2]):
                gone[j] = min(gone.get(j, 1e9), visit_s[k2])
    names = []
    for j in order:
        t = G["towns"][j]
        x, y = stops_px[j]
        x, y = x / SS, y / SS
        if not (-40 < x < W + 40 and K.MAP_TOP - 40 < y < K.TEXT_FLOOR + 40):
            continue
        r, fpx, face = G["sizes"][j]
        age = s - visit_s[j]
        if hook or age < 0:                       # ahead: a faint dot at its size
            c = K.INK if hook else K.DOT
            rr = r * (0.8 if hook else 0.6)
            od.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(*c, 200 if hook else 160))
            if not (hook and j in (0, len(G["towns"]) - 1)):     # opening frame: start and finish only
                continue
            a = 1.0
        else:
            pop = 1 + 0.9 * max(0.0, 1 - age / 0.35)
            rr = r * pop
            od.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(*K.ACCENT, 255), outline=(*K.WATER, 255), width=2)
            keep = t["pop"] >= KEEP_POP
            a = 1.0 if keep else max(0.0, min(1.0, age / 0.12, (LABEL_S - age) / 0.2))   # quick fades: big names crowd (2026-10-16)
            if j in gone and not keep:
                a *= max(0.0, 1 - (s - gone[j]) / 0.1)
            if s > DRIVE_END:
                a *= max(0.0, 1 - (s - DRIVE_END) / 0.6)
        if a <= 0:
            continue
        names.append((j, x, y, a))
    for j, x, y, a in names:                      # names over every dot
        img, p = flat(j)
        if a < 1:
            img = img.copy()
            img.putalpha(img.getchannel("A").point(lambda v: int(v * a)))
        bx, by, _, _ = label_box(j, x, y)
        over.alpha_composite(img, (int(bx - p), int(by - p)))
    if visit_s[G["loop_at"]] <= s:                  # the 1937 bypass the loop skips: faint dashes
        K.dashed(od, [((x - cx) / cw * W + W / 2, (cy - y) / cw * W + H / 2) for x, y in G["bypass"][::4]],
                 (*K.MUTED, 150), 3, 14, 10)
    # One state banner at a time.
    live = [j for j in G["entries"] if 0 <= s - visit_s[j] <= STATE_S]
    fact_on = any(0 <= s - visit_s[k] <= FACT_S for k in G["famous"])
    for j in live[-1:] if not fact_on else []:          # a fun fact takes the banner's spot
        age = s - visit_s[j]
        if DRIVE_START <= s <= DRIVE_END:
            a = min(1.0, age / 0.15, (STATE_S - age) / 0.35)
            K.callout(od, W / 2, K.MAP_TOP + 150, STATE[G["towns"][j]["state"]].upper(), a)
    im.alpha_composite(over)

    n_vis = int(visited.sum())
    n_all = len(G["towns"])
    card_a = K.smoothstep((s - CARD_IN) / 0.8)
    K.header(d, "Every town on Route 66", "Chicago to Santa Monica")
    if card_a < 1:
        K.stats(d, [(K.clock(drive), "hours", False), (f"{P['miles'][i]:,.0f}", "miles driven", False),
                    (f"{n_vis}", f"of {n_all} towns", True)])
        done = collections.Counter(group(G["towns"][j]["state"]) for j in np.flatnonzero(visited))
        K.chips(d, [(g, done[g], G["totals"][g]) for g in GROUPS])
    K.watermark(d)

    if n_vis and DRIVE_START <= s <= DRIVE_END + 0.6:
        j = int(np.flatnonzero(visited).max())
        t = G["towns"][j]
        K.now_label(d, STATE[t["state"]], clean(t["label"]), f"population {t['pop']:,}" if t["pop"] else "")

    # Fun fact: one at a time, the newest, FACT_S after the drive reaches its town.
    live = [(visit_s[j], j) for j in G["famous"] if 0 <= s - visit_s[j] <= FACT_S]
    if live and DRIVE_START <= s <= DRIVE_END:
        v0, j = max(live)
        age = s - v0
        over2 = Image.new("RGBA", im.size, (0, 0, 0, 0))
        K.callout(ImageDraw.Draw(over2), W / 2, K.MAP_TOP + 150, G["famous"][j],         # in the state banner's spot
                  min(1.0, age / 0.15, (FACT_S - age) / 0.3))
        im.alpha_composite(over2)
    hook_a = 1 - K.smoothstep((s - (K.HOOK - 0.3)) / 0.5)
    hook_q(im, hook_a)
    answer_card(im, card_a, tour)
    return im.convert("RGB").tobytes()


def sound(tour: dict, path: Path) -> None:
    """The house sound (college video): a tick per town, a chime when a chip
    group finishes, a chord at Boston."""
    vs = plan(tour)["visit_s"]
    towns = tour["towns"]
    chimes = [max(vs[j] for j, t in enumerate(towns) if group(t["state"]) == g) for g in GROUPS]
    K.soundtrack(path, SECONDS, list(vs[:-1]), chimes, float(vs[-1]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["tiktok", "reels"], default="tiktok")
    ap.add_argument("--stills", help="comma-separated seconds; writes PNGs only")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--sound", action="store_true", help="ticks per town, chimes, a final chord")
    # Rendered in parts by experiments/render.ps1, then --join (stream copy, no re-encode).
    ap.add_argument("--part", help="k/n: render only the k-th of n equal parts (1-based)")
    ap.add_argument("--join", type=int, help="join n rendered parts into the final file")
    args = ap.parse_args()
    tour = json.load(open(CACHE / "tour.json", encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    if args.stills:
        init(tour, args.platform)
        for sec in map(float, args.stills.split(",")):
            i = min(int(sec * FPS), SECONDS * FPS - 1)
            Image.frombytes("RGB", (W, H), render(i)).save(OUT / f"{args.platform}_{sec:04.1f}.png")
        return
    slug = f"route66_towns_{args.platform}"
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
            it, pool = map(render, frames), None
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
