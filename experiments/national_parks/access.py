"""The boats and bush planes the road network can't do (run after walmart/build.py).

build.py drives to each boat park's dock and to Anchorage airport for the four bush parks
(inventory.py puts those stops there). This adds:
- boat parks: a passenger boat out to the island and back (BOAT_H each way, inventory.BOAT).
  The tour starts at Dry Tortugas, so that one is the boat back to Key West only.
- bush parks: one floatplane/bush-plane loop from Anchorage through all four and back, in the
  shortest order, at BUSH_MPH plus BUSH_GROUND_H at each landing.
Each stop's dot moves to the park itself (island visitor centre, airstrip).

    python experiments/national_parks/access.py     # cache/tour_build.json -> cache/tour.json
"""

from __future__ import annotations

import itertools
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
sys.path.insert(0, str(HERE.parent / "walmart"))
import flights as F  # noqa: E402

BUSH_MPH = 150                       # Cessna/Beaver cruise
BUSH_GROUND_H = 1.0                  # per landing: taxi, load, weather margin
BUSH_STRIP = {"LACL": "Port Alsworth", "KATM": "Brooks Camp", "KOVA": "Kobuk dunes", "GAAR": "Anaktuvuk Pass"}


def line(a, b, n=12):
    return [[a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n] for k in range(1, n + 1)]


def main() -> None:
    src = CACHE / "tour_build.json"
    if not src.exists():
        shutil.copy(CACHE / "tour.json", src)
    t = json.load(open(src, encoding="utf-8"))
    C, T, D, M = t["coords"], t["t"], t["dist"], t["mode"]
    stops, flights, ferries = t["stops"], t["flights"], t["ferries"]

    def insert(iv: int, k: int, pts, secs, metres, modes) -> float:
        """Put pts after vertex iv (stop k is reached there); shift everything later."""
        t_at, nt, nd, bt, bd = T[iv], [], [], T[iv], D[iv]
        for s, m in zip(secs, metres):
            bt, bd = bt + s, bd + m
            nt.append(bt)
            nd.append(bd)
        dt, n = bt - T[iv], len(pts)
        for j in range(iv + 1, len(T)):
            T[j] += dt
            D[j] += bd - D[iv]
        C[iv + 1:iv + 1], T[iv + 1:iv + 1], D[iv + 1:iv + 1], M[iv + 1:iv + 1] = pts, nt, nd, modes
        for s in stops[k + 1:]:
            s["t"] += dt
        for f in flights:
            if f["t0"] >= t_at - 1e-6:
                f["t0"] += dt
                f["t1"] += dt
        for f in ferries:
            if f["i0"] >= iv:
                f["i0"] += n
                f["i1"] += n
        return dt

    def vertex(k: int) -> int:
        return min(range(len(T)), key=lambda i: abs(T[i] - stops[k]["t"]))

    inv = json.load(open(CACHE / "stores.json", encoding="utf-8"))
    boat_h = 0.0
    new_flights: list[dict] = []

    # --- bush loop (done first: it is late in the tour, so earlier vertex indices stay put) ----
    ks = [k for k, s in enumerate(stops) if s["access"] == "bush"]
    assert ks == list(range(ks[0], ks[0] + len(ks))), "bush stops are consecutive (all at ANC)"
    anc = stops[ks[0]]["road"]
    by = {stops[k]["code"]: stops[k] for k in ks}

    def loop_mi(order):
        pts = [anc] + [by[c]["park"] for c in order] + [anc]
        return sum(F.km(a, b) for a, b in zip(pts, pts[1:])) / 1.609344

    best = min(itertools.permutations(by), key=loop_mi)
    iv = vertex(ks[0])
    pts, secs, prev = [], [], anc
    for c in list(best) + ["ANC"]:
        p = by[c]["park"] if c != "ANC" else anc
        pts.append(p)
        secs.append((F.km(prev, p) / 1.609344 / BUSH_MPH + BUSH_GROUND_H) * 3600)
        prev = p
    t_iv = T[iv]
    insert(iv, ks[-1], pts, secs, [0.0] * len(pts), ["flight"] * len(pts))
    clock, a, prev_code = t_iv, anc, "ANC"
    stops[ks[0]:ks[-1] + 1] = [by[c] for c in best]
    for c, p, s in zip(list(best) + ["ANC"], pts, secs):
        f = dict(frm=prev_code, to=c, a=a, b=p, t0=clock, t1=clock + s,
                 a_town=BUSH_STRIP.get(prev_code, "Anchorage"), b_town=BUSH_STRIP.get(c, "Anchorage"), bush=True)
        new_flights.append(f)
        if c != "ANC":
            by[c].update(t=clock + s, road=p, lon=p[0], lat=p[1], town=BUSH_STRIP[c])
        clock, a, prev_code = clock + s, p, c
    flights.extend(new_flights)
    bush_h = (clock - t_iv) / 3600

    # --- boats, latest first ------------------------------------------------------------------
    for k in sorted((k for k, s in enumerate(stops) if s["access"] == "boat"), key=lambda k: -stops[k]["t"]):
        s = stops[k]
        island, h, mi = s["park"], s["boat_h"], s["boat_mi"]
        iv = vertex(k)
        dock = C[iv]
        if k == 0:                                    # start on the island, boat back to the dock
            assert iv == 0
            for j in range(len(T)):
                T[j] += h * 3600
                D[j] += mi * 1609.344
            M[0] = "ferry"                            # the old start vertex (the dock) ends the boat
            C[0:0] = [island] + line(island, dock)[:-1]
            T[0:0] = [h * 3600 * j / 12 for j in range(12)]
            D[0:0] = [mi * 1609.344 * j / 12 for j in range(12)]
            M[0:0] = ["road"] + ["ferry"] * 11
            for st in stops[1:]:
                st["t"] += h * 3600
            for f in flights:
                f["t0"] += h * 3600
                f["t1"] += h * 3600
            for f in ferries:
                f["i0"] += 12
                f["i1"] += 12
            ferries.append(dict(i0=0, i1=12, name=f"Boat from {s['name']}"))
            s["t"] = 0.0
            boat_h += h
        else:
            step_t, step_d = h * 3600 / 12, mi * 1609.344 / 12
            insert(iv, k, line(dock, island) + line(island, dock), [step_t] * 24, [step_d] * 24, ["ferry"] * 24)
            ferries.append(dict(i0=iv, i1=iv + 12, name=f"Boat to {s['name']}"))
            ferries.append(dict(i0=iv + 12, i1=iv + 24, name=f"Boat from {s['name']}"))
            s["t"] += h * 3600                        # reached when the boat lands
            boat_h += 2 * h
        s.update(road=island, lon=island[0], lat=island[1])
    ferries.sort(key=lambda f: f["i0"])
    flights.sort(key=lambda f: f["t0"])

    t["hours"] = T[-1] / 3600
    t["fly_hours"] = sum(f["t1"] - f["t0"] for f in flights) / 3600
    t["bush_hours"], t["boat_hours"] = bush_h, boat_h
    t["miles"] = sum(D[i] - D[i - 1] for i in range(1, len(D)) if M[i] == "road") / 1609.344
    t["bush_order"] = list(best)
    t["bush_miles"] = loop_mi(best)
    json.dump(t, open(CACHE / "tour.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{len(stops)} parks: {t['hours']:.1f} h total; {t['miles']:,.0f} mi driven; "
          f"{boat_h:.2f} h of boats; bush loop {'-'.join(['ANC', *best, 'ANC'])} "
          f"{t['bush_miles']:,.0f} mi, {bush_h:.1f} h; flights {[(f['frm'], f['to']) for f in flights]}")
    assert all(b >= a for a, b in zip(T, T[1:])), "time must not go backwards"
    ts = [s["t"] for s in stops]
    assert all(b >= a for a, b in zip(ts, ts[1:])), "stops in time order"
    for f in flights:
        assert any(abs(T[i] - f["t1"]) < 1 and M[i] == "flight" for i in range(len(T))), f"flight {f['frm']}-{f['to']} lands on a vertex"


if __name__ == "__main__":
    main()
