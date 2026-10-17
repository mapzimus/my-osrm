"""Optimal Drives tour videos: the pieces every "every X" video shares.

reel_kit.py is the house style (colours, fonts, HUD, camera, encoder). This module is the
plumbing under a tour video: the map projection, the Natural Earth basemap, the route helpers,
metro runs, the viewer's comment card, the state table, and the command line that render.ps1
drives (docs/optimal-drives-format.md section 14). Before 2026-10-17 every video carried its own
copy of these (24 videos, byte-identical); new videos import them instead:

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import reel_kit as K
    import reel_tour as T

A video then writes only its own data (groups, chips, highlights), plan(), init(), render()
and the end card. Reference: experiments/chickfila/video.py. Start a new one with
experiments/new_video.py <slug>.
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
from PIL import Image, ImageDraw

import reel_kit as K

ROOT = Path(__file__).resolve().parents[1]
NE_DATA = ROOT / "data" / "naturalearth"
# State and province lines: Natural Earth 10m, fetched by college_tour/build.py (and the other
# build.py scripts) into that cache. The polygons in NE_DATA would draw every border twice.
STATES_LINES = ROOT / "experiments/college_tour/cache/states/ne_10m_admin_1_states_provinces_lines.shp"

# --- places ------------------------------------------------------------------------------

STATE_NAME = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
              "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
              "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
              "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
              "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
              "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
              "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
              "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
              "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
              "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
              "DC": "Washington, DC", "PR": "Puerto Rico",
              "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
              "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories",
              "NU": "Nunavut", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
              "SK": "Saskatchewan", "YT": "Yukon"}
NAME_CODE = {v: k for k, v in STATE_NAME.items()}
CENSUS_REGION = {**{c: "Northeast" for c in "ME NH VT MA RI CT NY NJ PA".split()},
                 **{c: "Midwest" for c in "OH MI IN IL WI MN IA MO ND SD NE KS".split()},
                 **{c: "West" for c in "MT ID WY CO NM AZ UT NV CA OR WA AK HI".split()},
                 **{c: "South" for c in "DE MD DC VA WV NC SC GA FL KY TN AL MS AR LA OK TX".split()}}


def code(state: str) -> str:
    """build.py stores full state names; inventory.py writes codes. Either -> 'WI'."""
    return state if len(state) == 2 else NAME_CODE[state]


def ferry_name(name: str) -> str:
    """OSM ferry names, tidied for the label."""
    return name.replace("Linconlville", "Lincolnville").replace(" - ", " – ").replace(" ↔ ", " – ")


# --- projection: Albers equal-area conic on a sphere, km ------------------------------------

R, LON0, P1, P2, PHI0 = 6371.0, -100.0, 29.5, 45.5, 37.5
_n = (math.sin(math.radians(P1)) + math.sin(math.radians(P2))) / 2
_C = math.cos(math.radians(P1)) ** 2 + 2 * _n * math.sin(math.radians(P1))
_rho0 = R * math.sqrt(_C - 2 * _n * math.sin(math.radians(PHI0))) / _n


def km(lon, lat):
    """(x, y) in km; (0, 0) is 100 W, 37.5 N. The same projection in every video, so a camera
    width in km means the same thing everywhere."""
    lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    rho = R * np.sqrt(_C - 2 * _n * np.sin(lat)) / _n
    th = _n * (lon - math.radians(LON0))
    return rho * np.sin(th), _rho0 - rho * np.cos(th)


# --- basemap ----------------------------------------------------------------------------------

def rings(path: Path, clip: tuple, tol: float, lines: bool = False) -> list[tuple[np.ndarray, tuple]]:
    """Polygon rings (or lines) of a shapefile inside `clip` (lon/lat box), in km, each with
    its bounding box so render() can skip what is off screen."""
    import shapefile
    from shapely.geometry import box, shape
    clip_g, out = box(*clip), []
    for shp in shapefile.Reader(str(path)).iterShapes():
        if shp.shapeType == shapefile.NULL:
            continue
        b = shp.bbox
        if b[0] > clip[2] or b[2] < clip[0] or b[1] > clip[3] or b[3] < clip[1]:
            continue
        g = shape(shp.__geo_interface__)
        if not g.intersects(clip_g):
            continue
        g = g.intersection(clip_g).simplify(tol)
        for p in getattr(g, "geoms", [g]):
            parts = ([p.exterior, *p.interiors] if p.geom_type == "Polygon"
                     else [p] if p.geom_type == "LineString" else [])
            for r in parts:
                xy = np.asarray(r.coords)
                if len(xy) > 2 or (lines and len(xy) > 1):
                    pk = np.column_stack(km(xy[:, 0], xy[:, 1]))
                    out.append((pk, (*pk.min(0), *pk.max(0))))
    return out


def basemap(clip: tuple) -> dict:
    """{"fine": (land, lakes, countries, states), "coarse": (...)}: render() picks "coarse"
    when the frame is over ~1,500 km wide."""
    out = {}
    for lvl, tol in (("fine", 0.006), ("coarse", 0.05)):
        out[lvl] = (rings(NE_DATA / "ne_10m_land" / "ne_10m_land.shp", clip, tol),
                    rings(NE_DATA / "ne_10m_lakes" / "ne_10m_lakes.shp", clip, tol),
                    rings(NE_DATA / "ne_10m_admin_0_countries" / "ne_10m_admin_0_countries.shp", clip, tol),
                    rings(STATES_LINES, clip, tol, lines=True))
    return out


# --- the route ---------------------------------------------------------------------------------

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


def near(st: list[dict], lon: float, lat: float, r_km: float) -> np.ndarray:
    """Indices of the stops within r_km of (lon, lat)."""
    k = math.cos(math.radians(lat))
    return np.array([j for j, x in enumerate(st)
                     if math.hypot((x["lon"] - lon) * k, x["lat"] - lat) * 111.2 < r_km], int)


def longest_run(idx: np.ndarray) -> list[int]:
    """The longest run of consecutive indices."""
    runs, run = [], [int(idx[0])]
    for a, b in zip(idx, idx[1:]):
        if b == a + 1:
            run.append(int(b))
        else:
            runs.append(run)
            run = [int(b)]
    return max(runs + [run], key=len)


def metro_runs(st: list[dict], metros: dict) -> list[tuple[str, int, int]]:
    """(name, first, last) stop index of each metro's longest run of consecutive stops.
    metros: {"Atlanta": (lon, lat, r_km)} or, for a twin metro, a tuple of such triples."""
    out = []
    for name, spec in metros.items():
        parts = spec if isinstance(spec[0], tuple) else (spec,)     # Central Florida: Tampa + Orlando
        longest = [longest_run(near(st, *part)) for part in parts]
        out.append((name, min(r[0] for r in longest), max(r[-1] for r in longest)))
    return sorted(out, key=lambda r: r[1])


# --- the viewer's request ------------------------------------------------------------------------

def comment_card(path: Path, width: int = K.W - 200) -> Image.Image:
    """The viewer's comment (Max's screenshot), rounded, on a thin border (culvers/ first)."""
    im = Image.open(path).convert("RGBA")
    im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), 24, fill=255)
    im.putalpha(mask)
    card = Image.new("RGBA", (im.width + 6, im.height + 6), (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle((0, 0, card.width - 1, card.height - 1), 27, fill=(*K.BORDER, 255))
    card.alpha_composite(im, (3, 3))
    return card


def logo(path: Path, box: tuple[int, int] = (420, 190)) -> Image.Image:
    """The subject's logo for the first frame (format doc 6a): transparent padding cropped,
    fitted inside `box` (width, height)."""
    lg = Image.open(path).convert("RGBA")
    lg = lg.crop(lg.getbbox())
    f = min(box[0] / lg.width, box[1] / lg.height)
    return lg.resize((max(1, round(lg.width * f)), max(1, round(lg.height * f))), Image.LANCZOS)


# --- the command line (format doc section 14) ------------------------------------------------------

def part_frames(n: int, part: str | None) -> range:
    """The frames of part "k/m" of an n-frame video (1-based k); all of them without a part."""
    if not part:
        return range(n)
    k, m = map(int, part.split("/"))
    if not 1 <= k <= m:
        raise ValueError(f"part {part}: k must be 1..m")
    return range((k - 1) * n // m, k * n // m)


def main(slug: str, seconds: int, init, render, sound=None, *, out: Path, cache: Path,
         prepare=None, workers: int = 2, sound_default: bool = False) -> None:
    """Run a video's command line. render.ps1 relies on these flags: --platform, --workers,
    --part k/n, --join n, --sound; --stills and --tour are for checking.

    init(tour, platform) runs once per worker; render(i) returns a frame's RGB bytes;
    sound(tour, wav_path) writes the soundtrack; prepare(tour, args) may fix the tour up
    first (e.g. the stores' address states). Output: out/<slug>_<platform>.mp4.
    """
    ap = argparse.ArgumentParser(description=f"Optimal Drives: {slug}")
    ap.add_argument("--platform", choices=sorted(K.PLATFORMS), default="tiktok")
    ap.add_argument("--stills", help="comma-separated seconds; writes PNGs only")
    ap.add_argument("--workers", type=int, default=workers, help="render.ps1 fits this to the free RAM")
    ap.add_argument("--tour", default=str(cache / "tour.json"), help="e.g. cache/sample50/tour.json for a trial")
    ap.add_argument("--part", help="k/n: render only the k-th of n equal parts (1-based)")
    ap.add_argument("--join", type=int, help="join n rendered parts into the final file")
    if sound_default:
        ap.add_argument("--silent", dest="sound", action="store_false", help="no ticks or chimes")
        ap.add_argument("--sound", action="store_true", help=argparse.SUPPRESS)   # render.ps1 -Sound passes it; on anyway
    else:
        # Silent by default (format doc section 10): add app audio when posting.
        ap.add_argument("--sound", action="store_true", help="ticks, chimes and a finish chord")
    args = ap.parse_args()
    if args.sound and sound is None:
        ap.error("this video has no soundtrack")
    tour = json.load(open(args.tour, encoding="utf-8"))
    if prepare:
        prepare(tour, args)
    out.mkdir(exist_ok=True)
    n = seconds * K.FPS
    if args.stills:
        init(tour, args.platform)
        for sec in map(float, args.stills.split(",")):
            i = min(int(sec * K.FPS), n - 1)
            Image.frombytes("RGB", (K.W, K.H), render(i)).save(out / f"{args.platform}_{sec:04.1f}.png")
        return
    name = f"{slug}_{args.platform}"
    silent, wav = out / f"{name}_silent.mp4", out / f"{name}_ticks.wav"
    t0 = time.time()
    if args.join:
        parts = [out / f"{name}_part{k}.mp4" for k in range(1, args.join + 1)]
        lst = out / f"{name}_parts.txt"
        lst.write_text("".join(f"file '{q.name}'\n" for q in parts))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(silent)], check=True)
        lst.unlink()
        for q in parts:
            q.unlink()
    else:
        frames = part_frames(n, args.part)
        if args.part:
            silent = out / f"{name}_part{args.part.split('/')[0]}.mp4"
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
    final = out / f"{name}.mp4"
    if args.sound:
        sound(tour, wav)
        K.mux(silent, wav, final)
        silent.unlink()
        wav.unlink()
    else:
        silent.replace(final)
    print("wrote", final, f"{time.time() - t0:.0f} s")
