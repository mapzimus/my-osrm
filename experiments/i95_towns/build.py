"""I-95 north to south, with the time the drive reaches each town.

Route: the engine's drive along I-95 from Houlton, ME to Miami, FL, held to
the OSM line (interstates/cache/pieces.json) by a via every VIA_KM. Towns:
cache/towns.json from inventory.py; each is reached where the route comes
closest to the point where I-95 enters it.

    python experiments/i95_towns/inventory.py
    python experiments/i95_towns/build.py

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
VIA_KM = 10      # keeps the drive on I-95 (the route stays within 200 m of its line)


def drive_i95(line: list) -> dict:
    """Re-drive I-95 southbound with a via every VIA_KM. The interstates
    video's 60 km vias let the engine take the Baltimore-Washington Parkway
    for 24 miles (5.6 km off I-95), so Maryland's towns popped up beside the car."""
    sys.path.insert(0, str(HERE.parent / "lighthouses"))
    spec = importlib.util.spec_from_file_location("interstates_build", HERE.parent / "interstates" / "build.py")
    ib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ib)
    ib.VIA_KM = VIA_KM
    return ib.drive(line)


def main() -> None:
    p = next(x for x in json.load(open(PIECES)) if x["num"] == "95")
    r = drive_i95(p["line"] if p["line"][0][1] > p["line"][-1][1] else p["line"][::-1])      # southbound
    coords = np.asarray(r["coords"])
    kt = r["h"] * 3600 / sum(r["seg_t"])
    kd = r["mi"] * 1609.344 / sum(r["seg_d"])
    t = np.concatenate([[0.0], np.cumsum(r["seg_t"]) * kt])
    dist = np.concatenate([[0.0], np.cumsum(r["seg_d"]) * kd])

    towns = json.load(open(CACHE / "towns.json"))
    k = math.cos(math.radians(38))
    xy = np.column_stack([coords[:, 0] * k, coords[:, 1]])
    last = 0
    for tw in towns:
        # Nearest route vertex at or after the previous town's: the route is
        # walked in order, so a town can't be reached before the one north of it.
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
