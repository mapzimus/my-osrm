"""I-90 west to east (Seattle to Boston), with the time the drive reaches each town.

Route: the engine's drive along I-90 from Seattle, WA to Boston, MA, held to
the OSM line (interstates/cache/pieces.json) by a via every VIA_KM. Towns:
cache/towns.json from inventory.py; each is reached where the route comes
closest to the point where I-90 enters it.

With --line-only (engine off) the drive is the OSM line itself at a flat
MOCK_MPH: good enough for layout stills, not for the numbers.

    python experiments/i90_towns/inventory.py
    python experiments/i90_towns/build.py

Writes cache/tour.json for video.py.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
PIECES = HERE.parent / "interstates" / "cache" / "pieces.json"
VIA_KM = 11      # 10 put a via on an I-94 ramp at Billings: a 12-mile out-and-back; 8 is over the via limit
MOCK_MPH = 65


def drive_i90(line: list) -> dict:
    """Re-drive I-90 eastbound with a via every VIA_KM (60 km vias let the
    engine leave I-95 for the Baltimore-Washington Parkway)."""
    sys.path.insert(0, str(HERE.parent / "lighthouses"))
    spec = importlib.util.spec_from_file_location("interstates_build", HERE.parent / "interstates" / "build.py")
    ib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ib)
    ib.VIA_KM = VIA_KM
    return ib.drive(line)


def line_only(line: list) -> dict:
    """The OSM line at MOCK_MPH, in drive()'s shape (engine off)."""
    c = np.asarray(line)
    lat = np.radians(c[:, 1])
    dx = np.radians(np.diff(c[:, 0])) * np.cos((lat[1:] + lat[:-1]) / 2)
    seg_d = (np.hypot(dx, np.diff(lat)) * 6_371_000).tolist()
    mi = sum(seg_d) / 1609.344
    return dict(coords=line, h=mi / MOCK_MPH, mi=mi, seg_t=[d / (MOCK_MPH * 1609.344 / 3600) for d in seg_d], seg_d=seg_d)


def main() -> None:
    p = next(x for x in json.load(open(PIECES)) if x["num"] == "90")
    line = p["line"] if p["line"][0][0] < p["line"][-1][0] else p["line"][::-1]      # eastbound
    r = line_only(line) if "--line-only" in sys.argv else drive_i90(line)
    coords = np.asarray(r["coords"])
    kt = r["h"] * 3600 / sum(r["seg_t"])
    kd = r["mi"] * 1609.344 / sum(r["seg_d"])
    t = np.concatenate([[0.0], np.cumsum(r["seg_t"]) * kt])
    dist = np.concatenate([[0.0], np.cumsum(r["seg_d"]) * kd])

    towns = json.load(open(CACHE / "towns.json"))
    k = math.cos(math.radians(44))
    xy = np.column_stack([coords[:, 0] * k, coords[:, 1]])
    last = 0
    for tw in towns:
        # Nearest route vertex at or after the previous town's: the route is
        # walked in order, so a town can't be reached before the one west of it.
        q = np.array([tw["road"][0] * k, tw["road"][1]])
        d2 = ((xy[last:] - q) ** 2).sum(1)
        j = last + int(d2.argmin())
        tw["t"], tw["mi"], tw["lon"], tw["lat"] = float(t[j]), float(dist[j] / 1609.344), *map(float, coords[j])
        last = j
    states = list(dict.fromkeys(tw["state"] for tw in towns))
    json.dump(dict(coords=coords.tolist(), t=t.tolist(), dist=dist.tolist(), mode=["interstate"] * len(coords),
                   stops=[dict(name=tw["label"], lon=tw["lon"], lat=tw["lat"], t=tw["t"], region="US") for tw in towns],
                   towns=towns, states=states, flights=[], ferries=[], fly_hours=0.0,
                   hours=float(t[-1] / 3600), miles=float(dist[-1] / 1609.344)),
              open(CACHE / "tour.json", "w"))
    print(f"{len(towns)} towns, {len(states)} states ({' '.join(states)}), {t[-1] / 3600:.1f} h, "
          f"{dist[-1] / 1609.344:,.0f} mi, {towns[0]['label']} -> {towns[-1]['label']}")


if __name__ == "__main__":
    main()
