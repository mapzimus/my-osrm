"""Optimal Drives: every college in New England. A 65 s reel; the camera
follows the fastest drive through every campus, with clock, miles and count.

House style comes from experiments/reel_kit.py (spec: docs/optimal-drives-format.md).
Run build.py first.

    python experiments/college_tour/video.py
    python experiments/college_tour/video.py --stills 0,3,20,40,64   # PNGs only
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

W, H, FPS, SS = K.W, K.H, K.FPS, 2
SECONDS = 65
DRIVE_START = K.HOOK + K.DIVE
DRIVE_END = SECONDS - K.END_CARD - K.PULL_OUT - 0.5
CARD_IN = DRIVE_END + K.PULL_OUT - 0.4
LON0, LAT0 = -71.5, 43.5
KX, KY = math.cos(math.radians(LAT0)) * 111.32, 110.57
CLIP = (-82.0, 36.0, -58.0, 52.0)
CAMPUS_MIN, SLEEP_H = 20, 8           # real-life trip assumptions (end card)

STATE_ORDER = ["ME", "NH", "VT", "MA", "RI", "CT"]
STATE_NAME = {"ME": "Maine", "NH": "New Hampshire", "VT": "Vermont",
              "MA": "Massachusetts", "RI": "Rhode Island", "CT": "Connecticut"}
FAMOUS = {"Harvard University": "Harvard", "Massachusetts Institute of Technology": "MIT",
          "Yale University": "Yale", "Brown University": "Brown", "Dartmouth College": "Dartmouth",
          "Boston College": "Boston College", "Boston University": "BU",
          "Northeastern University": "Northeastern", "Tufts University": "Tufts",
          "Williams College": "Williams", "Amherst College": "Amherst",
          "University of Massachusetts-Amherst": "UMass Amherst", "Wellesley College": "Wellesley",
          "Bowdoin College": "Bowdoin", "Middlebury College": "Middlebury",
          "University of Vermont": "UVM", "Smith College": "Smith", "Wesleyan University": "Wesleyan",
          "Berklee College of Music": "Berklee", "University of Connecticut": "UConn",
          "University of Maine": "UMaine", "University of New Hampshire-Main Campus": "UNH",
          "University of Rhode Island": "URI", "United States Coast Guard Academy": "Coast Guard Academy",
          "Rhode Island School of Design": "RISD", "College of the Holy Cross": "Holy Cross",
          "Bates College": "Bates", "Colby College": "Colby"}
CALLOUT_S = 1.3


def km(lon, lat):
    return (np.asarray(lon) - LON0) * KX, (np.asarray(lat) - LAT0) * KY


def short(name: str) -> str:
    for a, b in (("University of Connecticut", "UConn"), ("University of Massachusetts", "UMass"),
                 ("University of Maine", "UMaine"), ("Community College", "CC"),
                 ("Community-Technical College", "CC")):
        name = name.replace(a, b)
    return name


# --- basemap ---------------------------------------------------------------

def rings(path: Path, clip) -> list[np.ndarray]:
    """Polygon rings (or lines) in km, clipped to the map area."""
    out = []
    for shp in shapefile.Reader(str(path)).iterShapes():
        if shp.shapeType == shapefile.NULL:
            continue
        b = shp.bbox
        if b[0] > CLIP[2] or b[2] < CLIP[0] or b[1] > CLIP[3] or b[3] < CLIP[1]:
            continue
        g = shape(shp.__geo_interface__)
        if not g.intersects(clip):
            continue
        g = g.intersection(clip).simplify(0.004)
        for p in getattr(g, "geoms", [g]):
            if p.geom_type == "Polygon":
                parts = [p.exterior, *p.interiors]
            elif p.geom_type == "LineString":
                parts = [p]
            else:
                continue
            for r in parts:
                xy = np.asarray(r.coords)
                if len(xy) > 2:
                    out.append(np.column_stack(km(xy[:, 0], xy[:, 1])))
    return out


def basemap():
    clip = box(*CLIP)
    return (rings(NE_DATA / "ne_10m_land" / "ne_10m_land.shp", clip),
            rings(NE_DATA / "ne_10m_lakes" / "ne_10m_lakes.shp", clip),
            rings(NE_DATA / "ne_10m_admin_0_countries" / "ne_10m_admin_0_countries.shp", clip),
            rings(CACHE / "states" / "ne_10m_admin_1_states_provinces_lines.shp", clip))


# --- timeline ----------------------------------------------------------------

def end_rows(tour: dict) -> tuple[list[tuple[str, str]], list[str]]:
    st, legs = tour["stops"], tour["legs"]
    lj = max(range(len(legs)), key=lambda q: legs[q]["h"])
    lm = round(legs[lj]["h"] * 60)
    awake = tour["hours"] + CAMPUS_MIN / 60 * len(st)
    days = awake / (24 - SLEEP_H)
    rows = [(f"{tour['hours']:.1f} hours", "of driving, nonstop"),
            (f"{tour['miles']:,.0f} miles", f"{len(st)} colleges, 6 states"),
            (f"{lm // 60} h {lm % 60:02d} min", f"longest hop: {st[lj]['city']} to {st[lj + 1]['city']}"),
            (f"{days:.1f} days", f"with {CAMPUS_MIN} min per campus, {SLEEP_H} h sleep a night")]
    footer = [f"Start: {short(st[0]['name'])}   Finish: {short(st[-1]['name'])}",
              "Colleges: IPEDS 2024   Routing: OSRM, OpenStreetMap"]
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
    drive = np.zeros(n)
    drive[f0:f1 + 1] = K.warp(stop_t, total, f1 + 1 - f0)
    drive[f1 + 1:] = total
    car_x, car_y = np.interp(drive, t, cx), np.interp(drive, t, cy)
    # Video second each stop is reached (pop, callout, tick).
    visit_s = np.searchsorted(drive[f0:f1 + 1], stop_t - 1e-9) / FPS + DRIVE_START

    # Follow camera: zoom widens where the car covers ground fast and tightens
    # in the clusters; centre smoothed so it never jitters.
    look = int(2.0 * FPS)
    span = np.empty(n)
    for i in range(n):
        a, b = max(0, i - look), min(n, i + look + 1)
        span[i] = max(np.ptp(car_x[a:b]), np.ptp(car_y[a:b]) * W / (K.MAP_BOTTOM - K.MAP_TOP))
    width = np.clip(span * 1.7 + 45, 70, 380)
    width = np.exp(gaussian_filter1d(np.log(width), FPS * 1.0, mode="nearest"))
    fx = gaussian_filter1d(car_x, FPS * 0.35, mode="nearest")
    fy = gaussian_filter1d(car_y, FPS * 0.35, mode="nearest")

    sx, sy = km([s["lon"] for s in tour["stops"]], [s["lat"] for s in tour["stops"]])
    allx, ally = np.concatenate([cx, sx]), np.concatenate([cy, sy])
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
                miles=np.interp(drive, t, dist), total=total, visit_s=visit_s)


# --- frame -------------------------------------------------------------------

G: dict = {}


def init(tour: dict) -> None:
    G["tour"] = tour
    G["base"] = basemap()
    G["plan"] = plan(tour)
    c = np.asarray(tour["coords"])
    G["route"] = np.column_stack(km(c[:, 0], c[:, 1]))
    st = tour["stops"]
    G["stops"] = np.column_stack(km([s["road"][0] for s in st], [s["road"][1] for s in st]))
    G["totals"] = {k: sum(s["st"] == k for s in st) for k in STATE_ORDER}
    G["famous"] = {j: FAMOUS[s["name"]] for j, s in enumerate(st) if s["name"] in FAMOUS}
    G["shade"] = K.shade()
    G["end"] = end_rows(tour)


def render(i: int) -> bytes:
    tour, P = G["tour"], G["plan"]
    land, lakes, borders, states = G["base"]
    s = i / FPS
    cx, cy, cw = P["cams"][i]
    scale = W * SS / cw
    ch = cw * H / W

    def px(xy: np.ndarray) -> list:
        x = (xy[:, 0] - cx) * scale + W * SS / 2
        y = (cy - xy[:, 1]) * scale + H * SS / 2
        return list(zip(x.tolist(), y.tolist()))

    def visible(xy: np.ndarray) -> bool:
        return (xy[:, 0].max() > cx - cw / 2 and xy[:, 0].min() < cx + cw / 2
                and xy[:, 1].max() > cy - ch / 2 and xy[:, 1].min() < cy + ch / 2)

    im = Image.new("RGB", (W * SS, H * SS), K.WATER)
    d = ImageDraw.Draw(im)
    for r in land:
        if visible(r):
            d.polygon(px(r), fill=K.LAND)
    for r in lakes:
        if visible(r):
            d.polygon(px(r), fill=K.WATER)
    for r in states + borders:
        if visible(r):
            d.line(px(r), fill=K.BORDER, width=max(1, int(SS * 1.6)))

    drive = P["drive"][i]
    k = int(P["idx"][i])
    car = np.array([[P["car"][0][i], P["car"][1][i]]])
    if drive > 0:
        pts = px(np.vstack([G["route"][:k], car]))
        d.line(pts, fill=K.ACCENT_GLOW, width=int(SS * 9), joint="curve")
        d.line(pts, fill=K.ACCENT, width=int(SS * 3.6), joint="curve")

    # Dots: grey until reached, then amber with a short pop.
    visit_s = P["visit_s"]
    visited = visit_s <= s
    zoom = min(1.6, max(1.0, (380 / cw) ** 0.35))
    stops_px = px(G["stops"])
    for j, ((x, y), v) in enumerate(zip(stops_px, visited)):
        if v:
            pop = 1 + 1.3 * max(0.0, 1 - (s - visit_s[j]) / 0.5)
            r, fill = SS * 6.5 * zoom * pop, K.ACCENT
        else:
            r, fill = SS * 5.5 * zoom, K.DOT
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=K.WATER, width=SS)
    if K.HOOK <= s <= DRIVE_END + 0.2:
        (x, y), = px(car)
        for r, c in ((SS * 16, K.ACCENT_GLOW), (SS * 9, K.INK)):
            d.ellipse((x - r, y - r, x + r, y + r), fill=c)

    im = im.resize((W, H), Image.LANCZOS).convert("RGBA")
    im.alpha_composite(G["shade"])
    d = ImageDraw.Draw(im)

    # Callouts for famous campuses, just after the car reaches them.
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    for j, label in G["famous"].items():
        age = s - visit_s[j]
        if 0 <= age <= CALLOUT_S and s <= DRIVE_END:
            a = min(1.0, age / 0.15, (CALLOUT_S - age) / 0.3)
            x, y = stops_px[j]
            K.callout(od, x / SS, y / SS, label, a)
    im.alpha_composite(over)

    n_vis = int(visited.sum())
    card_a = K.smoothstep((s - CARD_IN) / 0.8)
    K.header(d, "Every college in New England", f"{len(tour['stops'])} campuses, one drive, the fastest order")
    if card_a < 1:
        K.stats(d, [(K.clock(drive), "hours driving", False), (f"{P['miles'][i]:,.0f}", "miles", False),
                    (f"{n_vis}", f"of {len(tour['stops'])} colleges", True)])
        done = {st: 0 for st in STATE_ORDER}
        for j in np.flatnonzero(visited):
            done[tour["stops"][j]["st"]] += 1
        K.chips(d, [(st, done[st], G["totals"][st]) for st in STATE_ORDER])
    K.watermark(d)

    if n_vis and DRIVE_START <= s <= DRIVE_END + 0.6:
        j = int(np.flatnonzero(visited).max())
        stp = tour["stops"][j]
        kicker = "Start" if j == 0 else ("Finish" if j == len(tour["stops"]) - 1 else f"College {j + 1}")
        K.now_label(d, kicker, short(stp["name"]), f"{stp['city']}, {STATE_NAME[stp['st']]}")

    hook_a = 1 - K.smoothstep((s - (K.HOOK - 0.3)) / 0.5)
    K.hook(im, [f"{len(tour['stops'])} colleges.", f"{tour['hours']:.1f} hours."],
           "The fastest drive through every college in New England", hook_a)
    K.end_card(im, *G["end"], card_a)
    return im.convert("RGB").tobytes()


def sound(tour: dict, path: Path) -> None:
    vs = plan(tour)["visit_s"]
    st = [s["st"] for s in tour["stops"]]
    chimes = [max(vs[j] for j in range(len(st)) if st[j] == k) for k in STATE_ORDER]
    K.soundtrack(path, SECONDS, list(vs[:-1]), chimes, float(vs[-1]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stills", help="comma-separated seconds; writes PNGs only")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    tour = json.load(open(CACHE / "tour.json"))
    OUT.mkdir(exist_ok=True)
    if args.stills:
        init(tour)
        for sec in map(float, args.stills.split(",")):
            i = min(int(sec * FPS), SECONDS * FPS - 1)
            Image.frombytes("RGB", (W, H), render(i)).save(OUT / f"still_{sec:04.1f}.png")
        return
    silent, wav = OUT / "college_tour_silent.mp4", OUT / "college_tour_ticks.wav"
    ff = K.encoder(silent)
    n, t0 = SECONDS * FPS, time.time()
    with Pool(args.workers, initializer=init, initargs=(tour,)) as pool:
        for i, frame in enumerate(pool.imap(render, range(n), chunksize=1)):
            ff.stdin.write(frame)
            if i % 300 == 0:
                print(f"{i}/{n} frames, {time.time() - t0:.0f} s", flush=True)
    ff.stdin.close()
    if ff.wait():
        sys.exit(ff.returncode)
    sound(tour, wav)
    out = OUT / "college_tour_reel.mp4"
    K.mux(silent, wav, out)
    silent.unlink()
    print("wrote", out, f"{time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
