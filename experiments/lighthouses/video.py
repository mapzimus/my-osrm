"""Optimal Drives: every lighthouse you can drive to (lower 48, Alaska, Hawaii).
A 65 s reel per platform; the camera follows the trip with clock, miles and
count. Ferries sail as dashed lines with a boat; Hawaii is reached by plane.

House style comes from experiments/reel_kit.py (spec: docs/optimal-drives-format.md).
Run inventory.py, lastmile.py and build.py first.

    python experiments/lighthouses/video.py --platform tiktok
    python experiments/lighthouses/video.py --platform reels
    python experiments/lighthouses/video.py --platform tiktok --stills 0,3,20,40,64
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
STOP_MIN, SLEEP_H = 15, 8              # real-life trip assumptions (end card)
CALLOUT_S = 1.2
FLIGHT_STOPS = 10                      # a flight paces like this many stops (screen time)

GROUPS = ["ATL", "GULF", "LAKES", "PAC", "AK", "HI"]
GROUP_NAME = {"ATL": "Atlantic", "GULF": "Gulf", "LAKES": "Great Lakes", "PAC": "Pacific",
              "AK": "Alaska", "HI": "Hawaii"}
# Callouts: a word in the lighthouse name -> on-map label.
FAMOUS = {"portland head": "Portland Head", "cape hatteras": "Cape Hatteras", "pemaquid": "Pemaquid Point",
          "cape neddick": "Nubble Light", "nubble": "Nubble Light", "west quoddy": "West Quoddy Head",
          "split rock": "Split Rock", "point reyes": "Point Reyes", "heceta": "Heceta Head",
          "yaquina head": "Yaquina Head", "montauk": "Montauk Point", "barnegat": "Barnegat",
          "cape may": "Cape May", "bodie island": "Bodie Island", "st. augustine": "St. Augustine",
          "ponce de leon": "Ponce Inlet", "key west": "Key West", "pigeon point": "Pigeon Point",
          "old point loma": "Point Loma", "diamond head": "Diamond Head", "makapuu": "Makapuʻu",
          "kilauea": "Kīlauea Point", "marblehead": "Marblehead", "big sable": "Big Sable",
          "point betsie": "Point Betsie", "bass harbor": "Bass Harbor", "nauset": "Nauset",
          "sandy hook": "Sandy Hook", "cape henry": "Cape Henry", "tybee": "Tybee Island",
          "cape blanco": "Cape Blanco", "point arena": "Point Arena", "cape disappointment": "Cape Disappointment",
          "currituck": "Currituck", "cape lookout": "Cape Lookout", "point vicente": "Point Vicente",
          "grand traverse": "Grand Traverse", "point iroquois": "Point Iroquois", "sitka": "Sitka"}


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


def group(s: dict) -> str:
    if s["region"] in ("AK", "HI"):
        return s["region"]
    lon, lat = s["lon"], s["lat"]
    if lon < -114:
        return "PAC"
    if lat > 41.2 and -92.5 < lon < (-75.2 if lat > 43.9 else -75.9):
        return "LAKES"
    if (lat < 31 and lon < -81.5) or (lat < 25.3 and lon < -80.4):
        return "GULF"
    return "ATL"


def ferry_name(name: str) -> str:
    """OSM ferry names, tidied for the label."""
    return name.replace("Linconlville", "Lincolnville").replace(" - ", " – ").replace(" ↔ ", " – ")


def famous(name: str) -> str | None:
    n = name.lower()
    return next((v for k, v in FAMOUS.items() if k in n), None)


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
    rows = [(f"{drive_h:,.0f} hours", "of driving, nonstop"),
            (f"{tour['miles']:,.0f} miles", f"{len(st)} lighthouses, {n_states} states, {len(tour['ferries'])} ferries"),
            (f"{len(fl)} flights", f"Hawaii: 550 mph + 2 h at the airport, {tour['fly_hours']:.0f} h"),
            (f"{days:.0f} days", f"with {STOP_MIN} min per lighthouse, {SLEEP_H} h sleep a night")]
    footer = [f"Start: {st[0]['name']}", f"Finish: {st[-1]['name']}",
              "Lighthouses: OpenStreetMap, Wikidata   Routing: OSRM"]
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
    look = int(2.0 * FPS)
    span = np.empty(n)
    for i in range(n):
        a, b = max(0, i - look), min(n, i + look + 1)
        span[i] = max(np.ptp(car_x[a:b]), np.ptp(car_y[a:b]) * W / (K.MAP_BOTTOM - K.MAP_TOP))
    for fl in tour["flights"]:
        (ax, bx), (ay, by) = km([fl["a"][0], fl["b"][0]], [fl["a"][1], fl["b"][1]])
        m = (drive > fl["t0"] / 3600 - 0.5) & (drive < fl["t1"] / 3600 + 0.5)
        span[m] = np.maximum(span[m], max(abs(bx - ax), abs(by - ay) * W / (K.MAP_BOTTOM - K.MAP_TOP)) * 0.75)
    width = np.clip(span * 1.7 + 60, 90, 6000)
    width = np.exp(gaussian_filter1d(np.log(width), FPS * 1.0, mode="nearest"))
    fx = gaussian_filter1d(car_x, FPS * 0.45, mode="nearest")
    fy = gaussian_filter1d(car_y, FPS * 0.45, mode="nearest")

    # Opening and closing views frame the lower 48; Hawaii shows up by plane.
    l48 = [s for s in tour["stops"] if s["region"] == "L48"]
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
    town = {x["name"]: x["town"] for x in tour["stops"]}
    for fl in tour["flights"]:
        fl["a_town"], fl["b_town"] = town[fl["frm"]], town[fl["to"]]
    st = tour["stops"]
    G["stops"] = np.column_stack(km([s["lon"] for s in st], [s["lat"] for s in st]))
    G["group"] = [group(s) for s in st]
    G["totals"] = {g: G["group"].count(g) for g in GROUPS}
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

    drive = P["drive"][i]
    k = int(P["idx"][i])
    car = np.array([[P["car"][0][i], P["car"][1][i]]])
    q = int(P["flying"][i])
    plane = None
    if drive > 0:
        route = G["route"]
        lvl = "coarse" if cw > 1500 else "fine"
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
            r, fill = SS * 5.5 * zoom * pop, K.ACCENT
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
    for j, label in G["famous"].items():
        age = s - visit_s[j]
        if 0 <= age <= CALLOUT_S and s <= DRIVE_END:
            a = min(1.0, age / 0.15, (CALLOUT_S - age) / 0.3)
            x, y = stops_px[j]
            K.callout(od, x / SS, y / SS, label, a)
    im.alpha_composite(over)

    n_vis = int(visited.sum())
    n_all = len(tour["stops"])
    card_a = K.smoothstep((s - CARD_IN) / 0.8)
    K.header(d, "Every lighthouse you can drive to", "Lower 48 by road, Hawaii by plane")
    if card_a < 1:
        K.stats(d, [(K.clock(drive), "hours", False), (f"{P['miles'][i]:,.0f}", "miles driven", False),
                    (f"{n_vis}", f"of {n_all} lighthouses", True)])
        done = {g: 0 for g in GROUPS}
        for j in np.flatnonzero(visited):
            done[G["group"][j]] += 1
        K.chips(d, [(g, done[g], G["totals"][g]) for g in GROUPS if G["totals"][g]])
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
            kicker = "Start" if j == 0 else ("Finish" if j == n_all - 1 else f"Lighthouse {j + 1}")
            K.now_label(d, kicker, stp["name"], f"{stp['town']}, {stp['state']}")

    hook_a = 1 - K.smoothstep((s - (K.HOOK - 0.3)) / 0.5)
    K.hook(im, [f"{n_all} lighthouses.", f"{(tour['hours'] - tour['fly_hours']):,.0f} hours."],
           "The fastest drive to every lighthouse you can reach", hook_a, center_y=K.TEXT_FLOOR - 230)
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
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    tour = json.load(open(CACHE / "tour.json"))
    OUT.mkdir(exist_ok=True)
    if args.stills:
        init(tour, args.platform)
        for sec in map(float, args.stills.split(",")):
            i = min(int(sec * FPS), SECONDS * FPS - 1)
            Image.frombytes("RGB", (W, H), render(i)).save(OUT / f"{args.platform}_{sec:04.1f}.png")
        return
    slug = f"lighthouses_{args.platform}"
    silent, wav = OUT / f"{slug}_silent.mp4", OUT / f"{slug}_ticks.wav"
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
    sound(tour, wav)
    out = OUT / f"{slug}.mp4"
    K.mux(silent, wav, out)
    silent.unlink()
    print("wrote", out, f"{time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
