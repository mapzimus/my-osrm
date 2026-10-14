"""Fastest trip to every Walmart in the 50 states and Puerto Rico.

Stores: cache/stores.json (inventory.py). Matrix: matrix.py, cached blocks.
No road between two stores (Hawaii, Puerto Rico, Hawaii's islands): fly
(flights.py). Island crossings carry a solver-only penalty so each island
group is one visit, not a shortcut. Order: LKH-3 (lkh.py), free start and end.
Drive time only: no shopping, sleep or traffic.

    python experiments/walmart/build.py               # cached steps are skipped
    python experiments/walmart/build.py --sample 50   # trial: 50 stores near Atlanta, in cache/sample50/
    python experiments/walmart/build.py --resolve     # re-run the solver

Writes cache/tour.json (or cache/sampleN/tour.json) for video.py.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path

import numpy as np

import flights as F
import lkh
import matrix

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("parks_build", HERE.parent / "amusement_parks" / "build.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

SOLVE_SECONDS = 1200
ISLAND_PENALTY = 1_000_000            # seconds, solver only
ROUTE_CHUNK = 400                     # engine's max-viaroute-size is 500
MAX_DRIVE_LEG_H = 12


def bad_rows(dur: np.ndarray, frac: float = 0.5) -> list[int]:
    """Stores the engine can't leave or can't reach from most others."""
    n = len(dur)
    miss = np.isnan(dur).sum(1) + np.isnan(dur).sum(0)
    return [int(i) for i in np.flatnonzero(miss > frac * n)]


def chunks(n: int, size: int = ROUTE_CHUNK) -> list[tuple[int, int]]:
    """Inclusive index ranges of at most size stops; each starts where the last ended."""
    out, a = [], 0
    while True:
        b = min(a + size - 1, n - 1)
        out.append((a, b))
        if b == n - 1:
            return out
        a = b


def check_tour(tour: dict, n: int) -> None:
    names = [s["name"] + str(s.get("lon", "")) + str(s.get("lat", "")) for s in tour["stops"]]
    assert len(names) == n and len(set(names)) == n, "every store exactly once"
    ts = [s["t"] for s in tour["stops"]]
    assert all(b >= a for a, b in zip(ts, ts[1:])), "arrival times increase"
    assert abs(tour["t"][-1] / 3600 - tour["hours"]) < 1e-6, "clock ends at the total"
    # Alaska is reached by road through Canada (~40 h from Washington): exempt.
    assert all(leg["h"] <= MAX_DRIVE_LEG_H or leg.get("alaska") for leg in tour["legs"]), "no driving leg over 12 h"


def region(s: dict) -> str:
    if s["region"] == "puerto-rico":
        return "PR"
    return "HI" if -161 < s["lon"] < -154 and 18 < s["lat"] < 23 else "US"


def resnap(stores, dur, dist, road, bad):
    """Move each bad store to the nearest road that works both ways with a
    well-connected store; patch its row and column."""
    ok = int(np.argmin(np.isnan(dur).sum(1)))
    for i in bad:
        s = stores[i]
        for w in P.get(f"{P.OSRM}/nearest/v1/driving/{s['lon']},{s['lat']}?number=12")["waypoints"][1:]:
            d = P.get(f"{P.OSRM}/table/v1/driving/polyline({P.polyline([w['location'], road[ok]])})"
                      "?annotations=duration")["durations"]
            if d[0][1] is not None and d[1][0] is not None:
                road[i] = w["location"]
                pts = [road[i]] + road
                for kind, q in (("row", f"sources=0&destinations={';'.join(map(str, range(1, len(pts))))}"),
                                ("col", f"sources={';'.join(map(str, range(1, len(pts))))}&destinations=0")):
                    r = P.get(f"{P.OSRM}/table/v1/driving/polyline({P.polyline(pts)})?{q}"
                              "&annotations=duration,distance")
                    t = np.array(r["durations"], dtype=np.float64).ravel().astype(np.float32)
                    dd = np.array(r["distances"], dtype=np.float64).ravel().astype(np.float32)
                    if kind == "row":
                        dur[i, :], dist[i, :] = t, dd
                    else:
                        dur[:, i], dist[:, i] = t, dd
                print(f"  resnapped {s['name']} ({s['lon']:.4f}, {s['lat']:.4f}): {w['distance']:.0f} m")
                break


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int)
    ap.add_argument("--resolve", action="store_true")
    args = ap.parse_args()

    cache = HERE / "cache"
    stores = json.load(open(cache / "stores.json", encoding="utf-8"))
    if args.sample:
        atl = (-84.39, 33.75)
        stores = sorted(stores, key=lambda s: F.km((s["lon"], s["lat"]), atl))[:args.sample]
        cache = cache / f"sample{args.sample}"
        cache.mkdir(exist_ok=True)
    stores.sort(key=lambda s: (s["lon"], s["lat"]))
    for s in stores:
        s["region"] = region(s)
    n = len(stores)

    dur, dist, road = matrix.table([[s["lon"], s["lat"]] for s in stores], P.get, P.polyline, P.OSRM,
                                   cache / "matrix")
    bad = [i for i in bad_rows(dur) if stores[i]["region"] == "US"]
    if bad:
        resnap(stores, dur, dist, road, bad)
    still = [i for i in bad_rows(dur) if stores[i]["region"] == "US"]
    if still:
        print("dropped (no road connection):", [(stores[i]["name"], stores[i]["lon"], stores[i]["lat"]) for i in still])
        keep = [i for i in range(n) if i not in still]
        stores = [stores[i] for i in keep]
        dur, dist = dur[np.ix_(keep, keep)], dist[np.ix_(keep, keep)]
        road = [road[i] for i in keep]
        n = len(stores)
    for s, r in zip(stores, road):
        s["road"] = r

    # Different Hawaiian islands: no car ferry, so no road.
    isl = P.islands(stores)
    hi = [i for i in range(n) if isl[i] is not None]
    for a in hi:
        for b in hi:
            if isl[a] != isl[b]:
                dur[a, b] = dist[a, b] = np.nan

    codes = list(F.AIRPORTS)
    apts = [list(F.AIRPORTS[c]) for c in codes]
    reg = [s["region"] for s in stores]
    need = sorted({i for i in range(n) if reg[i] != "US"})
    to = frm = None                   # only used for no-road pairs, which need island stores
    if need:
        # Drive times store <-> airport for the stores that fly, and airport <-> every store.
        am_to, _, _ = matrix.table([s["road"] for s in stores] + apts, P.get, P.polyline, P.OSRM, cache / "air")
        to, frm = am_to[:n, n:], am_to[n:, :n]
    cost = dur.astype(np.float64)
    pen = 0
    for i, j in zip(*np.nonzero(np.isnan(dur))):
        c, _ = F.best_hop(i, j, reg[i], reg[j], to, frm, codes)
        both_islands = reg[i] != "US" and reg[j] != "US" and reg[i] != reg[j]
        cost[i, j] = c + (ISLAND_PENALTY if reg[i] != reg[j] else 0) + (10 * ISLAND_PENALTY if both_islands else 0)
        pen += 1
    print(f"{n} stores, {pen:,} no-road pairs priced as flights")

    op = cache / "order.json"
    if args.resolve or not op.exists() or len(json.load(open(op))) != n:
        order = lkh.solve_open(cost, [i for i in range(n) if reg[i] != "US"], cache / "lkh", SOLVE_SECONDS)
        json.dump(order, open(op, "w"))
    order = json.load(open(op))
    stops = [stores[i] for i in order]

    # Timeline: driving runs split at flights; each run routed in chunks.
    coords, t, dists, mode, flights, ferries, legs_info = [], [], [], [], [], [], []
    clock = metres = 0.0

    def push(c, dt, dd, md):
        nonlocal clock, metres
        clock += dt
        metres += dd
        coords.append(c)
        t.append(clock)
        dists.append(metres)
        mode.append(md)

    def drive_run(rs):
        for a, b in chunks(len(rs)):
            part = rs[a:b + 1]
            if len(part) < 2:
                continue
            for li, leg in enumerate(P.drive(part)):
                kt = leg["t"] / max(sum(leg["seg_t"]), 1e-9)
                kd = leg["d"] / max(sum(leg["seg_d"]), 1e-9)
                for c, dt, dd, fe in zip(leg["coords"][1:], leg["seg_t"], leg["seg_d"], leg["ferry"][1:]):
                    push(c, dt * kt, dd * kd, "ferry" if fe else "road")
                    if fe:
                        if ferries and ferries[-1]["i1"] == len(coords) - 2:
                            ferries[-1]["i1"] = len(coords) - 1
                        else:
                            ferries.append(dict(i0=len(coords) - 2, i1=len(coords) - 1, names={}))
                        ferries[-1]["names"][fe] = ferries[-1]["names"].get(fe, 0) + dd
                legs_info.append(dict(frm=part[li]["name"], to=part[li + 1]["name"], h=leg["t"] / 3600,
                                      mi=leg["d"] / 1609.344, ferry=any(leg["ferry"]),
                                      alaska=min(part[li]["road"][0], part[li + 1]["road"][0]) < -129))
                part[li + 1]["t"] = clock

    run = [stops[0]]
    push(stops[0]["road"], 0, 0, "road")
    stops[0]["t"] = 0.0
    for k in range(1, n):
        i, j = order[k - 1], order[k]
        if not np.isnan(dur[i, j]):
            run.append(stops[k])
            continue
        drive_run(run)
        _, (ca, cb) = F.best_hop(i, j, reg[i], reg[j], to, frm, codes)
        pa = [float(v) for v in apts[codes.index(ca)]]
        pb = [float(v) for v in apts[codes.index(cb)]]
        drive_run([stops[k - 1], dict(name=ca, road=pa)])
        t0 = clock
        push(pb, F.flight_s(F.AIRPORTS[ca], F.AIRPORTS[cb]), 0, "flight")
        flights.append(dict(frm=ca, to=cb, a=pa, b=pb, t0=t0, t1=clock, a_town=ca, b_town=cb))
        run = [dict(name=cb, road=pb), stops[k]]
    drive_run(run)

    for f in ferries:
        named = {k: v for k, v in f.pop("names").items()
                 if k != "Ferry" and not any(w in k for w in ("Approach", "Berth", "Ramp", "Terminal"))}
        f["name"] = max(named, key=named.get) if named else "Ferry"
    P.label(stops)
    hours = clock / 3600
    fly_h = sum(f["t1"] - f["t0"] for f in flights) / 3600
    tour = dict(stops=stops, coords=coords, t=t, dist=dists, mode=mode, flights=flights, ferries=ferries,
                legs=legs_info, hours=hours, fly_hours=fly_h, miles=metres / 1609.344,
                flown_miles=sum(F.km(f["a"], f["b"]) for f in flights) / 1.609344)
    check_tour(tour, n)
    json.dump(tour, open(cache / "tour.json", "w"))
    print(f"{n} stores, {hours:.1f} h ({hours - fly_h:.1f} h driving, {len(flights)} flights {fly_h:.1f} h), "
          f"{metres / 1609.344:,.0f} mi, {len(ferries)} ferries, {stops[0]['name']} -> {stops[-1]['name']}")


if __name__ == "__main__":
    main()
