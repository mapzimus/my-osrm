"""Real-world access for the two stops the road network can't reach (data probe, 2026-10-15).

walmart/build.py moves a stop with no road link to the nearest road it can reach, which put
Gosnold, MA on Martha's Vineyard and Manele, HI on Maui. Here, after build.py:
- Gosnold (Cuttyhunk): the drive ends at the Cuttyhunk ferry dock in New Bedford (inventory.py
  FIX); add the boat out and back, BOAT_H each way, drawn as a ferry.
- Manele (Lanai): fly into Lanai Airport (LNY) instead of Kahului (OGG), then drive LNY -> Manele
  (LANAI_DRIVE_H each way) instead of the Maui roads build.py chose.
Kupreanof, AK is reached at Petersburg, across the 1 km Wrangell Narrows: that is how you get
there (a skiff), so it stays, and the video says so.

    python experiments/smallest_towns/boats.py      # tour.json -> tour.json (keeps tour_build.json)
"""

from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
sys.path.insert(0, str(HERE.parent / "walmart"))
import flights as F  # noqa: E402

CUTTYHUNK = [-70.9265, 41.4250]
BOAT_H, BOAT_MI = 1.0, 16.0            # New Bedford - Cuttyhunk, each way
LNY = [-156.9514, 20.7856]
LANAI_DRIVE_H, LANAI_DRIVE_MI = 0.25, 9.0


def line(a, b, n=12):
    return [[a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n] for k in range(1, n + 1)]


def main() -> None:
    src = CACHE / "tour_build.json"
    if not src.exists():
        shutil.copy(CACHE / "tour.json", src)
    t = json.load(open(src, encoding="utf-8"))
    C, T, D, M = t["coords"], t["t"], t["dist"], t["mode"]

    def splice(i0: int, i1: int, pts, seconds: list[float], metres: list[float], modes: list[str]):
        """Replace vertices i0+1..i1 with pts (each with its own step time/distance); shift the rest."""
        base_t, base_d = T[i0], D[i0]
        nt, nd = [], []
        for s, m in zip(seconds, metres):
            base_t += s
            base_d += m
            nt.append(base_t)
            nd.append(base_d)
        dt, dd = base_t - T[i1], base_d - D[i1]
        n_new, n_old = len(pts), i1 - i0
        C[i0 + 1:i1 + 1], M[i0 + 1:i1 + 1] = pts, modes
        T[i0 + 1:i1 + 1], D[i0 + 1:i1 + 1] = nt, nd
        k = i0 + 1 + n_new
        for j in range(k, len(T)):
            T[j] += dt
            D[j] += dd
        shift = n_new - n_old
        for f in t["ferries"]:
            if f["i0"] > i0:
                f["i0"] += shift
                f["i1"] += shift
        return dt, dd, shift

    # --- Gosnold: boat out and back from the New Bedford dock ---------------------------------
    stops = t["stops"]
    g = next(k for k, s in enumerate(stops) if s["name"] == "Gosnold")
    ig = min(range(len(T)), key=lambda i: abs(T[i] - stops[g]["t"]))
    out, back = line(C[ig], CUTTYHUNK), line(CUTTYHUNK, C[ig])
    step = BOAT_H * 3600 / 12
    pts = out + back
    dt, dd, shift = splice(ig, ig, pts, [step] * 24, [BOAT_MI * 1609.344 / 12] * 24, ["ferry"] * 24)
    t["ferries"].append(dict(i0=ig, i1=ig + 12, name="Cuttyhunk Ferry (New Bedford)"))
    t["ferries"].append(dict(i0=ig + 12, i1=ig + 24, name="Cuttyhunk Ferry (New Bedford)"))
    for s in stops[g + 1:]:
        s["t"] += dt
    stops[g]["t"] += BOAT_H * 3600                      # "reached" when the boat lands on Cuttyhunk
    for fl in t["flights"]:
        fl["t0"] += dt
        fl["t1"] += dt
    t["ferries"].sort(key=lambda f: f["i0"])

    # --- Manele: land at Lanai (LNY), drive to Manele, fly out of LNY ---------------------------
    f1, f2 = t["flights"]
    F.AIRPORTS["LNY"] = tuple(LNY)
    i_land = next(i for i in range(len(T)) if M[i] == "flight" and abs(T[i] - f1["t1"]) < 1)
    i_take = next(i for i in range(len(T)) if M[i] == "flight" and abs(T[i] - f2["t1"]) < 1) - 1   # last vertex before flight 2
    h = next(k for k, s in enumerate(stops) if s["name"] == "Manele")
    man = [stops[h]["lon"], stops[h]["lat"]]
    # flight 1 now lands at LNY
    fly1 = F.flight_s(F.AIRPORTS[f1["frm"]], F.AIRPORTS["LNY"])
    C[i_land] = LNY
    d1 = (T[i_land - 1] + fly1) - T[i_land]
    for j in range(i_land, len(T)):
        T[j] += d1
    for s in stops[h:]:
        s["t"] += d1
    f1.update(to="LNY", b=LNY, t1=T[i_land], b_town="LNY")
    f2["t0"] += d1
    f2["t1"] += d1
    # LNY -> Manele -> LNY replaces the Maui drive
    pts = line(LNY, man, 6) + line(man, LNY, 6)
    step = LANAI_DRIVE_H * 3600 / 6
    dt2, dd2, _ = splice(i_land, i_take, pts, [step] * 12, [LANAI_DRIVE_MI * 1609.344 / 6] * 12, ["road"] * 12)
    stops[h]["t"] = T[i_land] + LANAI_DRIVE_H * 3600
    for s in stops[h + 1:]:
        s["t"] += dt2
    f2["t0"] += dt2
    f2["t1"] += dt2
    stops[h]["road"] = man
    # flight 2 now leaves from LNY
    i_land2 = next(i for i in range(len(T)) if M[i] == "flight" and i > i_land + 12)
    fly2 = F.flight_s(F.AIRPORTS["LNY"], F.AIRPORTS[f2["to"]])
    d3 = (T[i_land2 - 1] + fly2) - T[i_land2]
    for j in range(i_land2, len(T)):
        T[j] += d3
    for s in stops[h + 1:]:
        s["t"] += d3
    f2.update(frm="LNY", a=LNY, a_town="LNY", t0=T[i_land2 - 1], t1=T[i_land2])

    t["hours"] = T[-1] / 3600
    t["fly_hours"] = sum(f["t1"] - f["t0"] for f in t["flights"]) / 3600
    drive_m = sum(D[i] - D[i - 1] for i in range(1, len(D)) if M[i] == "road")
    t["miles"] = drive_m / 1609.344
    t["boat_hours"] = 2 * BOAT_H
    json.dump(t, open(CACHE / "tour.json", "w", encoding="utf-8"))
    print(f"{len(stops)} towns: {t['hours']:.1f} h total ({t['fly_hours']:.1f} h flying incl. 2 h at each airport, "
          f"{2 * BOAT_H:.0f} h by boat to Cuttyhunk), {t['miles']:,.0f} mi driven; "
          f"flights {[(f['frm'], f['to']) for f in t['flights']]}; ferries {len(t['ferries'])}")
    assert all(b >= a for a, b in zip(T, T[1:])), "time must not go backwards"


if __name__ == "__main__":
    main()
