"""Optimal Drives: every SUBJECT (SOURCE_NOTE). TEMPLATE: copy with experiments/new_video.py <slug>.

What this file is: the parts of a tour video that are the video's own. Everything shared is
imported: the house style from reel_kit (K) and the plumbing from reel_tour (T). Read
docs/optimal-drives-format.md first; experiments/chickfila/video.py is the full reference
(flights, metro dives, a highlight hold, the comment card, an answer card with bars).

Pipeline: inventory.py -> cache/stores.json; experiments/walmart/build.py --cache <here>/cache
-> cache/tour.json; this file -> out/<slug>_<platform>.mp4 through experiments/render.ps1.

    python experiments/SLUG/video.py --platform tiktok --stills 1,15,30,45,63   # check first
    .\\experiments\\render.ps1 SLUG                                              # then render
"""

from __future__ import annotations

import collections
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import reel_kit as K  # noqa: E402
import reel_tour as T  # noqa: E402

CACHE, OUT = HERE / "cache", HERE / "out"

FPS, SS = K.FPS, 2                     # SS: supersampling; frames are drawn at 2x and resized
W, H = K.W, K.H
SECONDS = 65
DRIVE_START = K.HOOK + K.DIVE
DRIVE_END = SECONDS - K.END_CARD - K.PULL_OUT - 0.5
CARD_IN = DRIVE_END + K.PULL_OUT - 0.4
CLIP = (-130.0, 23.0, -60.0, 52.0)     # lon/lat box the basemap is cut to: the lower 48 + margin
DAY_DRIVE_H = 10                       # end card: days at 10 h of driving a day

# --- the video's own data -----------------------------------------------------------------
TITLE, SUBTITLE = "Every SUBJECT", "The fastest drive to all of them"
QUESTION = ["How long to drive to", "every SUBJECT?"]           # frame 1; the last line in amber
NOUN, NOUNS = "SUBJECT", "SUBJECTS"                               # "Chick-fil-A #12", "of 3,415 restaurants"
SOURCE = "SUBJECT locator via AllThePlaces (MONTH YEAR)"
REQUESTED_BY = None                    # "parkermanh" for a viewer request (comment.jpg in assets/)
LOGO = HERE / "assets" / "logo.png"    # format doc 6a: official logo, ~400 px+ wide
COMMENT = HERE / "assets" / "comment.jpg"                        # the viewer's comment, if any
COMMENT_S = 3.4                        # on screen from the first frame
# Chips (at most 6): US Census regions, Canada if any. Suburbs read as their real city.
GROUPS = ["South", "West", "Midwest", "Northeast"]
CHIP = {"South": "S", "West": "W", "Midwest": "MW", "Northeast": "NE"}
# Metro dives: the longest run of consecutive stops within r km of a metro centre.
METROS = {}                            # {"Atlanta": (-84.4, 33.75, 50)}
DIVE_STOPS = 450                       # pseudo-stops per metro dive (~2 s on screen)
# Famous stops: ((lon, lat), callout text, hold the clock there?)
HIGHLIGHTS: list[tuple[tuple[float, float], str, bool]] = []
HOLD_S, HOLD_KM, CALLOUT_S = 1.6, 25, 1.8
ZOOM_RATE_MAX = K.ZOOM_RATE_MAX        # 2.6 when flights go to islands 4,000 km out
CARD_STATES = 6                        # answer card: bars for the top states, then one for the rest

km, code = T.km, T.code


def group(s: dict) -> str:
    return T.CENSUS_REGION.get(code(s["state"]), "South")


# --- timeline ----------------------------------------------------------------------------------

def highlights(st: list[dict]) -> list[tuple[int, str, bool]]:
    """(stop index, callout, hold) for each HIGHLIGHT: the nearest stop."""
    out = []
    for (lon, lat), text, hold in HIGHLIGHTS:
        j = min(range(len(st)), key=lambda j: (st[j]["lon"] - lon) ** 2 + (st[j]["lat"] - lat) ** 2)
        out.append((j, text, hold))
    return out


def plan(tour: dict):
    """Per-frame drive time, car position and camera (format doc 6: shots, not tracking)."""
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
    dives = T.metro_runs(st, METROS)
    holds = [j for j, _, hold in highlights(st) if hold]

    # Pacing: half the clock, half the stop count (K.warp); dives add pseudo-stops, holds
    # stop the clock. None of this is counted, it only shapes the pace.
    pace = list(stop_t)
    for _, a, b in dives:
        pace += list(np.linspace(stop_t[a], stop_t[b], DIVE_STOPS + 2)[1:-1])
    hold_n = int(HOLD_S * FPS)
    waits = [(stop_t[j], hold_n) for j in holds]
    core = K.hold_clock(K.warp(np.sort(pace), total, f1 + 1 - f0 - sum(w for _, w in waits)), waits)
    drive = np.zeros(n)
    drive[f0:f1 + 1] = core
    drive[f1 + 1:] = total
    car_x, car_y = np.interp(drive, t, cx), np.interp(drive, t, cy)
    visit_s = np.searchsorted(drive[f0:f1 + 1], stop_t - 1e-9) / FPS + DRIVE_START

    allx, ally = km([x["lon"] for x in st], [x["lat"] for x in st])
    ox, oy = (allx.min() + allx.max()) / 2, (ally.min() + ally.max()) / 2

    def fit(top: float, bottom: float) -> tuple[float, float]:
        return max(np.ptp(allx) * 1.08, np.ptp(ally) * W / ((bottom - top) * 0.94)), (top + bottom) / 2

    def frame_of(h: float) -> int:                            # first frame at drive time h
        return int(np.clip(np.searchsorted(drive, h - 1e-9), f0, f1))

    def route_cam(h0: float, h1: float, min_w: float):
        a, b = np.searchsorted(t, h0), np.searchsorted(t, h1, side="right")
        xs = np.concatenate([cx[a:b], car_x[frame_of(h0):frame_of(h1) + 1]])
        ys = np.concatenate([cy[a:b], car_y[frame_of(h0):frame_of(h1) + 1]])
        return K.fit_cam(xs, ys, min_w)

    # Fixed shots first (dives, holds; chickfila/ adds flights and islands); road shots fill the gaps.
    special = []
    for name, a, b in dives:
        xs, ys = km([x["lon"] for x in st[a:b + 1]], [x["lat"] for x in st[a:b + 1]])
        special.append((frame_of(stop_t[a]), frame_of(stop_t[b]), K.fit_cam(xs, ys, K.MIN_DIVE_KM)))
    for j in holds:
        (x,), (y,) = km([st[j]["lon"]], [st[j]["lat"]])
        i0 = frame_of(stop_t[j])
        special.append((max(f0, i0 - int(0.6 * FPS)), min(f1, i0 + hold_n + int(0.3 * FPS)),
                        K.fit_cam([x - HOLD_KM, x + HOLD_KM], [y - HOLD_KM, y + HOLD_KM], K.MIN_DIVE_KM)))
    shots = K.lay_shots(special, f0, f1, lambda i, e: route_cam(drive[i], drive[e], K.MIN_ROAD_KM))
    sx, sy, slw = K.camera_path(shots, n, f0, f1, car_x, car_y, zoom_rate=ZOOM_RATE_MAX)

    card_bottom = answer_card(Image.new("RGBA", (1, 1)), 0, tour)
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
                miles=np.interp(drive, t, dist), total=total, visit_s=visit_s, shots=shots,
                dives=[(nm, frame_of(stop_t[a]), frame_of(stop_t[b])) for nm, a, b in dives])


# --- frame ---------------------------------------------------------------------------------------

G: dict = {}


def init(tour: dict, platform: str) -> None:
    """Once per worker: everything render() reads."""
    K.use(platform)
    G["tour"] = tour
    G["base"] = T.basemap(CLIP)
    G["plan"] = plan(tour)
    c = np.asarray(tour["coords"])
    G["route"] = np.column_stack(km(c[:, 0], c[:, 1]))
    G["mode"] = np.asarray(tour["mode"])
    G["runs"] = T.split_runs(G["mode"])
    G["simple"] = {lvl: [T.simplified(G["route"][a:b], tol) for _, a, b in G["runs"]]
                   for lvl, tol in (("fine", 0.03), ("coarse", 0.6), ("national", 2.0))}
    st = tour["stops"]
    G["stops"] = np.column_stack(km([s["lon"] for s in st], [s["lat"] for s in st]))
    G["group"] = [group(s) for s in st]
    G["totals"] = {g: G["group"].count(g) for g in GROUPS}
    G["state_n"] = collections.Counter(s["state"] for s in st)
    seen = collections.Counter()
    G["state_done"] = []                  # stop j is the n-th stop reached in its state
    for x in st:
        seen[x["state"]] += 1
        G["state_done"].append(seen[x["state"]])
    G["highlights"] = highlights(st)
    G["shade"] = K.shade()
    G["comment"] = T.comment_card(COMMENT) if REQUESTED_BY else None
    G["logo"] = T.logo(LOGO, (420, 190 if REQUESTED_BY else 400))


WIN_RGB, REST_RGB = (0xC4, 0x87, 0x0B), (0x56, 0x5E, 0x6E)     # format doc 6a (validated on the card)


def hook_q(im: Image.Image, alpha: float) -> None:
    """First frame: the logo and the question (format doc 6a)."""
    if alpha <= 0:
        return
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    dd = ImageDraw.Draw(layer)
    a = int(255 * alpha)
    f0 = K.F("hook")
    size = f0.size
    while max(f0.font_variant(size=size).getlength(t) for t in QUESTION) > W - 90:
        size -= 4
    f = f0.font_variant(size=size)
    lh = int(size * 1.12)
    y0 = K.TEXT_FLOOR - 200 - len(QUESTION) * lh / 2
    dd.rectangle((0, y0 - 40, W, y0 + len(QUESTION) * lh + 40), fill=(0, 0, 0, int(170 * alpha)))
    for r, t in enumerate(QUESTION):
        dd.text((W / 2, y0 + r * lh), t, font=f, fill=(*(K.ACCENT if r == len(QUESTION) - 1 else K.INK), a),
                anchor="mt")
    lg = G["logo"]
    if alpha < 1:
        lg = lg.copy()
        lg.putalpha(lg.getchannel("A").point(lambda v: int(v * alpha)))
    cy = int(K.MAP_TOP + 14) + (G["comment"].height + 14 if G["comment"] else 40)
    layer.alpha_composite(lg, ((W - lg.width) // 2, cy))
    im.alpha_composite(layer)


def answer_card(im: Image.Image, alpha: float, tour: dict) -> int:
    """The answer (format doc 6a): hours big, then stops per state as bars for the top
    CARD_STATES (the winner gold, the rest grey), the other states as one line."""
    st = tour["stops"]
    per = collections.Counter(code(s["state"]) for s in st)
    order = sorted(per, key=per.get, reverse=True)
    rest, order = order[CARD_STATES:], order[:CARD_STATES]
    top, row_h = K.TOP + 150, 56
    bottom = top + 40 + 250 + len(order) * row_h + 56 + 30 + 36 * 2 + 50
    if alpha <= 0:
        return bottom
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((40, top, W - 40, bottom), 28, fill=K.CARD_FILL, outline=K.BORDER, width=2)
    y = top + 34
    d.text((W / 2, y), f"EVERY {NOUN.upper()}, BY CAR", font=K.F("tag"), fill=K.MUTED, anchor="mt")
    y += 46
    d.text((W / 2, y), f"{tour['hours']:.0f} hours", font=K.F("hook_sub").font_variant(size=96), fill=K.INK, anchor="mt")
    y += 118
    d.text((W / 2, y), f"{len(st):,} {NOUNS}  ·  {tour['miles']:,.0f} miles  ·  {st[0]['town']} to {st[-1]['town']}",
           font=K.F("small"), fill=K.MUTED, anchor="mt")
    y = top + 40 + 250
    x0, x1 = 330, W - 170
    for k, c in enumerate(order):
        yy = y + k * row_h
        d.text((x0 - 16, yy + 26), T.STATE_NAME[c], font=K.F("small"), fill=K.INK, anchor="rm")
        bw = max(8, (x1 - x0) * per[c] / per[order[0]])
        d.rounded_rectangle((x0, yy + 11, x0 + bw, yy + 41), 4, fill=WIN_RGB if k == 0 else REST_RGB)
        d.text((x0 + bw + 14, yy + 26), f"{per[c]}", font=K.F("small"), fill=K.INK, anchor="lm")
    y += len(order) * row_h
    if rest:
        d.text((W / 2, y + 14), f"+ {sum(per[c] for c in rest):,} more in {len(rest)} other states",
               font=K.F("small"), fill=K.MUTED, anchor="mt")
    y += 56 + 30
    by = f"   ·   Requested by {REQUESTED_BY}" if REQUESTED_BY else ""
    for line in [f"{NOUNS.capitalize()}: {SOURCE}", f"Routing: OSRM, no traffic{by}"]:
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
    if drive > 0:
        route = G["route"]
        lvl = "national" if cw > 3000 else "coarse" if cw > 1500 else "fine"
        roads = []
        for r, (kind, a, b) in enumerate(G["runs"]):
            if a >= k:
                break
            seg = G["simple"][lvl][r] if b <= k else route[a:k]
            if len(seg) < 2:
                continue
            pts = px(seg)
            if kind == "ferry":
                K.dashed(d, pts, K.ACCENT, int(SS * 3.6), SS * 14, SS * 10)
            else:
                roads.append(np.round(np.asarray(pts) * 16).astype(np.int32))
        if roads:                                             # one OpenCV pass: round joins, fast
            arr = np.array(im)
            cv2.polylines(arr, roads, False, K.ACCENT_GLOW, int(SS * 9), cv2.LINE_8, 4)
            cv2.polylines(arr, roads, False, K.ACCENT, int(SS * 3.6), cv2.LINE_8, 4)
            im = Image.fromarray(arr)
            d = ImageDraw.Draw(im)
        if k > 0:                                             # the open segment up to the car
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
            pop = 1 + 1.6 * max(0.0, 1 - (s - visit_s[j]) / 0.4)
            r, fill = SS * 2.6 * zoom * pop, K.ACCENT
        elif s < DRIVE_START:
            r, fill = SS * 2.2, K.INK
        else:
            r, fill = SS * 2.2 * zoom, K.DOT
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)

    # Car or boat.
    on_ferry = 0 < k < len(G["mode"]) and G["mode"][k] == "ferry"
    if K.HOOK <= s <= DRIVE_END + 0.2:
        (x, y), = px(car)
        if on_ferry:
            (x0, y0), = px(G["route"][k - 1:k])
            K.glyph(d, K.BOAT, x, y, math.atan2(y - y0, x - x0), SS * 1.6)
        else:
            for r, c in ((SS * 14, K.ACCENT_GLOW), (SS * 8, K.INK)):
                d.ellipse((x - r, y - r, x + r, y + r), fill=c)

    im = im.resize((W, H), Image.LANCZOS).convert("RGBA")
    im.alpha_composite(G["shade"])

    # Callouts: highlights for CALLOUT_S after the car gets there; metro names during a dive.
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    live = [(visit_s[j], j, text, hold) for j, text, hold in G["highlights"]
            if 0 <= s - visit_s[j] <= CALLOUT_S + (HOLD_S if hold else 0)]
    if live and s <= DRIVE_END:
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

    n_vis, n_all = int(visited.sum()), len(tour["stops"])
    card_a = K.smoothstep((s - CARD_IN) / 0.8)
    K.header(d, TITLE, SUBTITLE)
    if card_a < 1:
        K.stats(d, [(K.clock(drive), "hours", False), (f"{P['miles'][i]:,.0f}", "miles driven", False),
                    (f"{n_vis:,}", f"of {n_all:,} {NOUNS}", True)])
        done = {g: 0 for g in GROUPS}
        for j in np.flatnonzero(visited):
            done[G["group"][j]] += 1
        K.chips(d, [(CHIP[g], done[g], G["totals"][g]) for g in GROUPS if G["totals"][g]])
    under_comment = G["comment"] is not None and s < COMMENT_S + 0.4
    K.watermark(d, (1 - card_a) * (0.0 if under_comment else 1.0))

    if DRIVE_START <= s <= DRIVE_END + 0.6:
        if on_ferry:
            fe = next((f for f in tour["ferries"] if f["i0"] < k <= f["i1"]), None)
            mins = round((tour["t"][fe["i1"]] - tour["t"][fe["i0"]]) / 60) if fe else 0
            name = T.ferry_name(fe["name"]) if fe else "Ferry"
            K.now_label(d, "Ferry", name, f"{mins // 60} h {mins % 60:02d} min with the 30 min boarding wait"
                        if mins >= 60 else f"{mins} min with the 30 min boarding wait")
        elif n_vis:
            j = int(np.flatnonzero(visited).max())
            stp = tour["stops"][j]
            kicker = "Start" if j == 0 else ("Finish" if j == n_all - 1 else f"{NOUN} #{j + 1:,}")
            k_st, n_st = G["state_done"][j], G["state_n"][stp["state"]]
            K.now_label(d, kicker, stp["town"], f"{k_st} of {n_st} in {T.STATE_NAME[code(stp['state'])]}")

    hook_q(im, 1 - K.smoothstep((s - (K.HOOK - 0.3)) / 0.5))
    if G["comment"] is not None:
        ca = min(1.0, (COMMENT_S - s) / 0.4)
        if ca > 0:                                           # the request, over the hook
            card = G["comment"].copy()
            card.putalpha(card.getchannel("A").point(lambda v: int(v * ca)))
            im.alpha_composite(card, ((W - card.width) // 2, int(K.MAP_TOP + 14)))
    answer_card(im, card_a, tour)
    return im.convert("RGB").tobytes()


def sound(tour: dict, path: Path) -> None:
    """Opt-in (render.ps1 -Sound): a tick per stop, a chime per finished chip, a chord at the end."""
    vs = plan(tour)["visit_s"]
    g = [group(s) for s in tour["stops"]]
    chimes = [max(vs[j] for j in range(len(g)) if g[j] == k) for k in GROUPS if k in g]
    K.soundtrack(path, SECONDS, list(vs[:-1]), chimes, float(vs[-1]))


if __name__ == "__main__":
    T.main("SLUG", SECONDS, init, render, sound, out=OUT, cache=CACHE, workers=2)
