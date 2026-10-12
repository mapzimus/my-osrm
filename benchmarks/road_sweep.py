"""Non-ferry bug sweep on the live engine, every country in the build.

    python benchmarks/road_sweep.py [--url http://127.0.0.1:5002]

Cities/towns per country come from Overpass (cached in benchmarks/cities.json).
Checks:
  SNAP        town more than 2 km from any routable road (missing data / filter too strict)
  ONE-WAY     A reaches B but B can't reach A, or one direction > 1.5x slower (trap)
  CUT-OFF     mainland town unreachable from the country's biggest city
  DETOUR      road distance > 3.5x straight line for towns 30-400 km apart (missing link)
  SPEED       average > 125 km/h over a whole trip (speed tag bug)
  BORDER      a road border crossing that can't be driven both ways, or is lopsided
Writes benchmarks/road_sweep.md and road_sweep.json.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CITIES = HERE / "cities.json"
BORDERS = ROOT / "data" / "osrm-na-ferry" / "borders" / "border-report.json"
OVERPASS = "https://overpass-api.de/api/interpreter"

# ISO code -> (cap on towns, mainland?) ; island states are checked for snaps and
# internal connectivity only (no road to the mainland is expected).
COUNTRIES = {
    "US": (250, True), "CA": (150, True), "MX": (120, True),
    "GT": (25, True), "BZ": (15, True), "SV": (15, True), "HN": (20, True),
    "NI": (20, True), "CR": (20, True), "PA": (20, True),
    "CU": (30, False), "JM": (15, False), "HT": (15, False), "DO": (20, False),
    "BS": (15, False), "PR": (20, False), "TT": (10, False), "BB": (6, False),
    "BM": (5, False), "GL": (10, False), "KY": (5, False), "TC": (5, False),
    "AG": (5, False), "LC": (5, False), "VC": (5, False), "GD": (5, False),
    "DM": (5, False), "KN": (5, False), "VI": (5, False), "VG": (5, False),
    "AW": (5, False), "CW": (5, False), "MQ": (8, False), "GP": (8, False),
}
# Islands inside "mainland" countries: towns here are not expected to reach the capital.
ISLAND_BOXES = [  # (w, s, e, n)
    (-161, 18, -154, 23),        # Hawaii
    (-179, 51, -129, 72),        # Alaska (no road from lower 48 except via Canada -> fine, but
                                 # remote bush towns have no roads at all)
    (-130, 48, -123, 54.5),      # Vancouver Island / Haida Gwaii (ferry-only)
    (-64.5, 46, -59.5, 47.2),    # PEI + Cape Breton ok by bridge; Magdalen Is.
    (-59.5, 46.5, -52, 52),      # Newfoundland
    (-141, 60, -50, 84),         # northern Canada (fly-in)
    (-87.6, 20.2, -86.7, 21.7),  # Cozumel / Isla Mujeres
    (-71.5, 41, -69.8, 41.6),    # Martha's Vineyard / Nantucket
    (-82.95, 24.4, -80, 25.1),   # Keys connected; Dry Tortugas not
    (-83.5, 9, -82.0, 9.5),      # Bocas del Toro
    (-87, 16, -85.5, 16.6),      # Bay Islands Honduras
    (-84, 11.9, -82.5, 12.5),    # Corn Islands
    (-92, 46.7, -90.3, 48.2),    # Isle Royale / Madeline
    (-83, 45.5, -82, 46.0),      # Manitoulin side
]


def overpass(q: str) -> dict:
    req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
                                 headers={"User-Agent": "my-osrm-road-sweep/1.0"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=200) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  overpass retry {attempt + 1}: {e}", flush=True)
            time.sleep(30 * (attempt + 1))
    raise RuntimeError("overpass failed")


def pop(t: dict) -> int:
    try:
        return int(str(t.get("population", "0")).replace(",", "").split()[0].split(".")[0])
    except ValueError:
        return 0


def load_cities() -> dict[str, list[dict]]:
    have = json.loads(CITIES.read_text(encoding="utf-8")) if CITIES.exists() else {}
    for iso, (cap, _) in COUNTRIES.items():
        if iso in have:
            continue
        q = (f'[out:json][timeout:180];area["ISO3166-1"="{iso}"][admin_level=2]->.a;'
             f'node[place~"^(city|town)$"](area.a);out;')
        try:
            els = overpass(q)["elements"]
        except RuntimeError:
            print(f"  {iso}: Overpass unavailable, skipped (rerun later to add it)", flush=True)
            continue
        rows = [{"name": e["tags"].get("name", "?"), "lon": e["lon"], "lat": e["lat"],
                 "pop": pop(e["tags"]), "place": e["tags"]["place"]} for e in els]
        rows.sort(key=lambda r: (r["place"] != "city", -r["pop"]))
        # biggest first, then spread: keep every k-th of the rest for coverage
        head, rest = rows[:cap // 2], rows[cap // 2:]
        step = max(1, len(rest) // max(1, cap - len(head)))
        have[iso] = head + rest[::step][:cap - len(head)]
        print(f"  {iso}: {len(els)} places -> {len(have[iso])}", flush=True)
        CITIES.write_text(json.dumps(have, ensure_ascii=False, indent=0), encoding="utf-8")
        time.sleep(3)
    return have


def km(a, b) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    return math.hypot((b[0] - a[0]) * 111.32 * math.cos(lat), (b[1] - a[1]) * 110.54)


def get(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def in_island(p) -> bool:
    return any(w <= p[0] <= e and s <= p[1] <= n for w, s, e, n in ISLAND_BOXES)


def country_checks(url: str, iso: str, towns: list[dict], mainland: bool) -> list[dict]:
    flags = []
    pts = [(t["lon"], t["lat"]) for t in towns]
    coords = ";".join(f"{x:.5f},{y:.5f}" for x, y in pts)
    d = get(f"{url}/table/v1/driving/{coords}?annotations=duration,distance")
    if d.get("code") != "Ok":
        return [{"kind": "ERROR", "iso": iso, "what": d.get("message", d.get("code"))}]
    snaps = [w["distance"] for w in d["sources"]]
    dur, dist = d["durations"], d["distances"]
    n = len(towns)
    for i, t in enumerate(towns):
        if snaps[i] > 2000:
            flags.append({"kind": "SNAP", "iso": iso, "what": f"{t['name']} {snaps[i] / 1000:.1f} km from a road",
                          "at": pts[i]})
    ok = [i for i in range(n) if snaps[i] <= 2000]
    hub = ok[0] if ok else None
    for i in ok:
        for j in ok:
            if j <= i:
                continue
            a, b = dur[i][j], dur[j][i]
            ti, tj = towns[i]["name"], towns[j]["name"]
            if (a is None) != (b is None):
                flags.append({"kind": "ONE-WAY", "iso": iso, "what": f"{ti} <-> {tj}: only one direction routes",
                              "at": pts[i], "to": pts[j]})
                continue
            if a is None:
                continue
            fast, slow = min(a, b), max(a, b)
            if fast > 1800 and slow / fast > 1.5:
                flags.append({"kind": "ONE-WAY", "iso": iso,
                              "what": f"{ti} <-> {tj}: {a / 60:.0f} vs {b / 60:.0f} min", "at": pts[i], "to": pts[j]})
            s = km(pts[i], pts[j])
            if 30 < s < 400 and dist[i][j] / 1000 > 3.5 * s:
                flags.append({"kind": "DETOUR", "iso": iso,
                              "what": f"{ti} -> {tj}: {dist[i][j] / 1000:.0f} km road vs {s:.0f} km straight",
                              "at": pts[i], "to": pts[j]})
            if dist[i][j] > 50_000 and (dist[i][j] / 1000) / (a / 3600) > 125:  # 80 mph limits exist
                flags.append({"kind": "SPEED", "iso": iso,
                              "what": f"{ti} -> {tj}: avg {(dist[i][j] / 1000) / (a / 3600):.0f} km/h",
                              "at": pts[i], "to": pts[j]})
    if mainland and hub is not None:
        for j in ok:
            if dur[hub][j] is None and dur[j][hub] is None and not in_island(pts[j]):
                flags.append({"kind": "CUT-OFF", "iso": iso,
                              "what": f"{towns[j]['name']} unreachable from {towns[hub]['name']}", "at": pts[j]})
    return flags


def border_checks(url: str) -> list[dict]:
    rows = [r for r in json.load(open(BORDERS, encoding="utf-8")) if not r["ferry"]]
    d = 0.03  # ~3 km

    def one(r):
        # Plain 2-point routes across the crossing on 4 axes. A forced
        # waypoint ON the border node caused U-turns (charge counted 2-3x)
        # and test points in water/dead ends make single axes lopsided, so
        # flag only when NO axis gives a sane, symmetric pair.
        c = (r["lon"], r["lat"])
        pairs = []
        for dx, dy in ((0, 1), (1, 0), (1, 1), (1, -1)):
            a = (c[0] + dx * d / math.cos(math.radians(c[1])), c[1] + dy * d)
            b = (c[0] - dx * d / math.cos(math.radians(c[1])), c[1] - dy * d)
            res = []
            for p, q in ((a, b), (b, a)):
                j = get(f"{url}/route/v1/driving/{p[0]:.5f},{p[1]:.5f};{q[0]:.5f},{q[1]:.5f}?overview=false")
                res.append(j["routes"][0]["duration"] if j.get("code") == "Ok" else None)
            pairs.append(res)
        if any(f is not None and g is not None and max(f, g) <= 1.5 * max(min(f, g), 600)
               for f, g in pairs):
            return None
        shown = ", ".join("—" if None in p else f"{p[0] / 60:.0f}/{p[1] / 60:.0f}" for p in pairs)
        return {"kind": "BORDER", "iso": "--",
                "what": f"{r['name'] or 'unnamed'} (n{r['node']}): no symmetric axis, min each way {shown}",
                "at": [r["lon"], r["lat"]]}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [x for x in pool.map(one, rows) if x]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5002")
    args = ap.parse_args()
    cities = load_cities()
    flags = []
    for iso, (cap, mainland) in COUNTRIES.items():
        towns = cities.get(iso) or []
        if len(towns) < 2:
            flags.append({"kind": "NO-DATA", "iso": iso, "what": f"{len(towns)} towns from Overpass"})
            continue
        f = country_checks(args.url, iso, towns, mainland)
        print(f"  {iso}: {len(towns)} towns, {len(f)} flags", flush=True)
        flags += f
    b = border_checks(args.url)
    print(f"  borders: {len(b)} flags", flush=True)
    flags += b
    (HERE / "road_sweep.json").write_text(json.dumps(flags, indent=1, ensure_ascii=False), encoding="utf-8")
    kinds = sorted({f["kind"] for f in flags})
    lines = ["# Road sweep", "", " · ".join(f"{k} {sum(f['kind'] == k for f in flags)}" for k in kinds), "",
             "| Kind | Country | Finding |", "|---|---|---|"]
    lines += [f"| {f['kind']} | {f['iso']} | {f['what']} |" for f in sorted(flags, key=lambda f: (f["kind"], f["iso"]))]
    (HERE / "road_sweep.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:3]))


if __name__ == "__main__":
    main()
