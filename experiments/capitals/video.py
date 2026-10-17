"""Optimal Drives: every state, province and territory capital in the US,
Canada and Mexico (95 buildings). A 65 s reel per platform; the camera
follows the trip close in, with clock, miles and count. Iqaluit is a round
trip by plane; the flight to Honolulu is the finale. Adapted from
amusement_parks/video.py.

House style comes from experiments/reel_kit.py (spec: docs/optimal-drives-format.md).
Run inventory.py and build.py first.

    python experiments/capitals/video.py --platform tiktok
    python experiments/capitals/video.py --platform reels
    python experiments/capitals/video.py --platform tiktok --stills 0,3,20,40,64
"""

from __future__ import annotations

import argparse
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
# East edge -10, not -60: Newfoundland (to -52.6, St. John's) vanished at -60 (2026-10-14).
CLIP = (-172.0, 15.0, -10.0, 72.0)
STOP_MIN, SLEEP_H = 60, 8              # real-life trip assumptions (end card): 1 h per capitol
CALLOUT_S = 1.5
# The trail and the dots take the colour of the country they're in; the chips are the legend.
COLOR = {"US": K.ACCENT, "Canada": (239, 83, 80), "Mexico": (46, 204, 113)}
GLOW = {g: tuple(int(v * 0.47) for v in c) for g, c in COLOR.items()}
FLIGHT_STOPS = 10                      # a flight paces like this many stops (screen time)

GROUPS = ["US", "Canada", "Mexico"]
CHIP = {"US": "US", "Canada": "CANADA", "Mexico": "MEXICO"}
CALLOUT_S = 1.4

R, LON0, P1, P2, PHI0 = 6371.0, -100.0, 29.5, 45.5, 37.5
_n = (math.sin(math.radians(P1)) + math.sin(math.radians(P2))) / 2
_C = math.cos(math.radians(P1)) ** 2 + 2 * _n * math.sin(math.radians(P1))
_rho0 = R * math.sqrt(_C - 2 * _n * math.sin(math.radians(PHI0))) / _n


def km(lon, lat):
    lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    rho = R * np.sqrt(_C - 2 * _n * np.sin(lat)) / _n
    th = _n * (lon - math.radians(LON0))
    return rho * np.sin(th), _rho0 - rho * np.cos(th)


def group(s: dict) -> str:
    return {"US": "US", "CA": "Canada", "MX": "Mexico"}[s["country"]]


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
    st, fl = tour["stops"], tour["flights"]
    drive_h = tour["hours"] - tour["fly_hours"]
    awake = tour["hours"] + STOP_MIN / 60 * len(st)
    days = awake / (24 - SLEEP_H)
    n_states = len({s["state"] for s in st})
    isl = " and ".join(dict.fromkeys(("Puerto Rico" if f["b_town"] == "SJU" else "Hawaii")
                                     for f in fl if f["b_town"] in ("SJU", "HNL")))
    by = {c: sum(s["country"] == c for s in st) for c in ("US", "CA", "MX")}
    rows = [(f"{drive_h:,.0f} hours", "of driving, nonstop"),
            (f"{tour['miles']:,.0f} miles", f"{len(tour['ferries'])} ferries, {len(fl)} flights (Iqaluit, Honolulu)"),
            (f"{len(st)} capitals", f"{by['US']} US, {by['CA']} Canada, {by['MX']} Mexico"),
            (f"{days:.0f} days", f"with {STOP_MIN // 60} h per capitol, {SLEEP_H} h sleep a night")]
    footer = [f"Start: {st[0]['city']}, {st[0]['division']}", f"Finish: {st[-1]['city']}, {st[-1]['division']}",
              "Capitols: Wikidata (some palacios at the main square)   Routing: OSRM"]
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
    look = int(0.6 * FPS)
    span = np.empty(n)
    for i in range(n):
        a, b = max(0, i - look), min(n, i + look + 1)
        span[i] = max(np.ptp(car_x[a:b]), np.ptp(car_y[a:b]) * W / (K.MAP_BOTTOM - K.MAP_TOP))
    for fl in tour["flights"]:
        (ax, bx), (ay, by) = km([fl["a"][0], fl["b"][0]], [fl["a"][1], fl["b"][1]])
        m = (drive > fl["t0"] / 3600 - 0.5) & (drive < fl["t1"] / 3600 + 0.5)
        span[m] = np.maximum(span[m], max(abs(bx - ax), abs(by - ay) * W / (K.MAP_BOTTOM - K.MAP_TOP)) * 0.75)
    # Tight clusters zoom in: the frame also shrinks toward ~1.4x the current leg (stop to
    # stop), so short hops fill the screen. Never below 0.5x the car's own motion, so it
    # stays in frame; long legs and flights are unaffected (their leg is the bigger term).
    sx, sy = km([s["lon"] for s in tour["stops"]], [s["lat"] for s in tour["stops"]])
    nxt = np.clip(np.searchsorted(stop_t, drive), 1, len(stop_t) - 1)
    leg = np.hypot(sx[nxt] - sx[nxt - 1], sy[nxt] - sy[nxt - 1])
    leg[flying >= 0] = np.inf
    width = np.clip(np.minimum(span * 0.9 + 20, np.maximum(leg * 1.4, span * 0.5 + 15)), 40, 6000)
    width = np.exp(gaussian_filter1d(np.log(width), FPS * 0.6, mode="nearest"))
    fx = gaussian_filter1d(car_x, FPS * 0.3, mode="nearest")
    fy = gaussian_filter1d(car_y, FPS * 0.3, mode="nearest")

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
    G["ctry"] = countries(c)
    G["runs"] = split_runs(G["mode"], G["ctry"])
    G["simple"] = {lvl: [simplified(G["route"][a:b], tol) for _, a, b, _ in G["runs"]]
                   for lvl, tol in (("fine", 0.03), ("coarse", 0.6))}
    G["flight_v"] = list(np.flatnonzero(G["mode"] == "flight"))     # route vertex of each flight
    town = {x["name"]: x["town"] for x in tour["stops"]}
    for fl in tour["flights"]:
        fl.setdefault("a_town", town.get(fl["frm"]))      # build.py names the airports
        fl.setdefault("b_town", town.get(fl["to"]))
    st = tour["stops"]
    G["stops"] = np.column_stack(km([s["lon"] for s in st], [s["lat"] for s in st]))
    G["group"] = [group(s) for s in st]
    G["totals"] = {g: G["group"].count(g) for g in GROUPS}
    G["famous"] = {j: s["city"] for j, s in enumerate(st)}          # every capital gets a callout
    G["shade"] = K.shade()
    G["end"] = end_rows(tour)


def countries(coords: np.ndarray) -> np.ndarray:
    """Group ("US", "Canada", "Mexico") of each route vertex. Vertices off land (ferries,
    flights over water) keep the country they left from. Cached: the test is ~1 min."""
    path = CACHE / "route_country.npy"
    if path.exists():
        out = np.load(path)
        if len(out) == len(coords):
            return out
    import shapely
    r = shapefile.Reader(str(NE_DATA / "ne_10m_admin_0_countries" / "ne_10m_admin_0_countries.shp"))
    a3 = [f[0] for f in r.fields[1:]].index("ADM0_A3")
    out = np.full(len(coords), "", dtype="<U6")
    for rec, shp in zip(r.iterRecords(), r.iterShapes()):
        g = {"USA": "US", "CAN": "Canada", "MEX": "Mexico"}.get(rec[a3])
        if g:
            poly = shape(shp.__geo_interface__)
            shapely.prepare(poly)
            out[shapely.contains_xy(poly, coords[:, 0], coords[:, 1])] = g
    last = next(v for v in out if v)
    for i, v in enumerate(out):
        last = out[i] = v or last
    np.save(path, out)
    return out


def split_runs(mode: np.ndarray, ctry: np.ndarray) -> list[tuple[str, int, int, str]]:
    """(kind, a, b, country): route vertices a..b-1 travelled by road, ferry or flight in one country.
    mode[i] is how the segment ending at vertex i was travelled; ctry[i] is where vertex i is."""
    out, a = [], 0
    for i in range(1, len(mode)):
        if i == len(mode) - 1 or mode[i + 1] != mode[i] or ctry[i + 1] != ctry[i]:
            out.append((str(mode[i]), a, i + 1, str(ctry[i])))
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
        lvl = "coarse" if cw > 1500 else "fine"
        for r, (kind, a, b, g) in enumerate(G["runs"]):
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
                K.dashed(d, pts, COLOR[g], int(SS * 3.6), SS * 14, SS * 10)
            else:
                d.line(pts, fill=GLOW[g], width=int(SS * 9), joint="curve")
                d.line(pts, fill=COLOR[g], width=int(SS * 3.6), joint="curve")
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
            col = COLOR[G["ctry"][min(k, len(G["ctry"]) - 1)]]
            if G["mode"][min(k, len(G["mode"]) - 1)] == "ferry":
                K.dashed(d, pts, col, int(SS * 3.6), SS * 14, SS * 10)
            else:
                d.line(pts, fill=col, width=int(SS * 3.6))

    # Dots: grey until reached, then amber with a short pop.
    visit_s = P["visit_s"]
    visited = visit_s <= s
    zoom = min(1.5, max(0.7, (400 / cw) ** 0.3))
    stops_px = px(G["stops"])
    for j, ((x, y), v) in enumerate(zip(stops_px, visited)):
        if not (-50 < x < W * SS + 50 and -50 < y < H * SS + 50):
            continue
        if v:
            pop = 1 + 1.3 * max(0.0, 1 - (s - visit_s[j]) / 0.5)
            r, fill = SS * 6.5 * zoom * pop, COLOR[G["group"][j]]
        elif s < DRIVE_START:
            # Hook and dive: every lighthouse lit up so the coasts read as a thumbnail.
            r, fill = SS * 4.5, K.INK
        else:
            r, fill = SS * 4.5 * zoom, K.DOT
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
    # Every capital gets a box. Newest first; an older box that would cover a placed one
    # steps up out of the way, and one under the HUD or the now-label is pulled into the map.
    live = sorted((j for j in G["famous"] if 0 <= s - visit_s[j] <= CALLOUT_S and s <= DRIVE_END),
                  key=lambda j: -visit_s[j])
    placed = []
    for j in live:
        age, label = s - visit_s[j], G["famous"][j]
        a = min(1.0, age / 0.15, (CALLOUT_S - age) / 0.3)
        x, y = stops_px[j]
        if not (0 <= x / SS <= W and K.MAP_TOP - 80 <= y / SS <= K.NOW_TOP + 40):
            continue                                           # camera has moved on; no orphan box
        y = min(max(y / SS, K.MAP_TOP + 40), K.NOW_TOP - 60)
        l, t, r, b = K.callout_box(x / SS, y, label)
        for _ in range(6):
            hit = next((q for q in placed if l < q[2] and r > q[0] and t < q[3] and b > q[1]), None)
            if not hit:
                break
            t, b = hit[1] - 6 - (b - t), hit[1] - 6
        if t < K.MAP_TOP:
            continue                                           # nowhere left to put it this frame
        placed.append((l, t, r, b))
        K.callout(od, x / SS, y, label, a, color=COLOR[G["group"][j]], box=(l, t, r, b))
    im.alpha_composite(over)

    n_vis = int(visited.sum())
    n_all = len(tour["stops"])
    card_a = K.smoothstep((s - CARD_IN) / 0.8)
    K.header(d, "Every state & provincial capital", "US, Canada and Mexico: 95 capitol buildings")
    if card_a < 1:
        K.stats(d, [(K.clock(drive), "hours", False), (f"{P['miles'][i]:,.0f}", "miles driven", False),
                    (f"{n_vis}", f"of {n_all} capitals", True)])
        done = {g: 0 for g in GROUPS}
        for j in np.flatnonzero(visited):
            done[G["group"][j]] += 1
        K.chips(d, [(CHIP[g], done[g], G["totals"][g]) for g in GROUPS if G["totals"][g]],
                colors=[COLOR[g] for g in GROUPS if G["totals"][g]])
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
            kicker = "Start" if j == 0 else ("Finish" if j == n_all - 1 else f"Capital {j + 1}")
            K.now_label(d, kicker, stp["building"], f"{stp['city']}, {stp['division']}")

    hook_a = 1 - K.smoothstep((s - (K.HOOK - 0.3)) / 0.5)
    K.hook(im, [f"{n_all} capitals.", f"{(tour['hours'] - tour['fly_hours']):,.0f} hours."],
           "The fastest drive to every state & provincial capital", hook_a, center_y=K.TEXT_FLOOR - 230)
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
    # Off by default, but Max picked ticks & chimes for the capitals cut (2026-10-14).
    ap.add_argument("--sound", action="store_true", help="ticks per capital, chimes, a final chord")
    # Low memory (2026-10-14, 1.4 GB free): render in parts, each short enough to
    # finish in one sitting, then --join them (stream copy, no re-encode).
    ap.add_argument("--part", help="k/n: render only the k-th of n equal parts (1-based)")
    ap.add_argument("--join", type=int, help="join n rendered parts into the final file")
    args = ap.parse_args()
    tour = json.load(open(CACHE / "tour.json"))
    OUT.mkdir(exist_ok=True)
    if args.stills:
        init(tour, args.platform)
        for sec in map(float, args.stills.split(",")):
            i = min(int(sec * FPS), SECONDS * FPS - 1)
            Image.frombytes("RGB", (W, H), render(i)).save(OUT / f"{args.platform}_{sec:04.1f}.png")
        return
    slug = f"capitals_{args.platform}"
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
