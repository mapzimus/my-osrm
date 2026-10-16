"""Export the national parks tour as an itinerary (Max, 2026-10-16: for the viewer who asked for the
video, who wants an ordered list of stops, the mode of each leg and the time between them).

    python experiments/national_parks/export.py      # -> Downloads\\national_parks_itinerary\\

Writes:
- itinerary.csv: one row per leg (park to park): how it's travelled (drive, ferry, boat, flight) with
  hours and miles of each, the leg total and the running total, plus each park's visitor centre.
- parks.csv: the 61 stops in order, with coordinates.
- route.gpx: every park as a waypoint and the whole route as a track (drive and ferry parts; flights
  as straight segments), for Google Earth or a nav app.
Times are the routing engine's drive times, no traffic, no stops; boat and bush-plane times are
estimates (access.py). Map data © OpenStreetMap contributors.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
OUT = Path.home() / "Downloads" / "national_parks_itinerary"
sys_path = HERE.parent
import sys  # noqa: E402
sys.path.insert(0, str(sys_path))
from national_parks.inventory import PICK  # noqa: E402


def main() -> None:
    t = json.load(open(HERE / "cache" / "tour.json", encoding="utf-8"))
    C, T, D, M = t["coords"], t["t"], t["dist"], t["mode"]
    stops = t["stops"]
    ferry_name = {}
    for f in t["ferries"]:
        for v in range(f["i0"] + 1, f["i1"] + 1):
            ferry_name[v] = f["name"]
    flight_at = {}
    for f in t["flights"]:
        v = min(range(len(T)), key=lambda i: abs(T[i] - f["t1"]))
        flight_at[v] = f
    # the vertex where each stop is reached
    sv, j = [], 0
    for s in stops:
        while j < len(T) - 1 and T[j] < s["t"] - 1:
            j += 1
        sv.append(j)
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for k in range(len(stops) - 1):
        a, b = sv[k], sv[k + 1]
        parts = []                                 # (mode, hours, miles, name) runs
        for v in range(a + 1, b + 1):
            if v in flight_at:
                f = flight_at[v]
                mode, name = ("bush plane" if f.get("bush") else "flight"), f"{f['a_town']} -> {f['b_town']}"
                mi = math.dist(C[v - 1], C[v]) * 69
            elif M[v] == "ferry":
                name = ferry_name.get(v, "ferry")
                mode = "boat" if name.startswith("Boat") else "ferry"
                mi = (D[v] - D[v - 1]) / 1609.344
            else:
                mode, name, mi = "drive", "", (D[v] - D[v - 1]) / 1609.344
            h = (T[v] - T[v - 1]) / 3600
            if parts and parts[-1][0] == mode and parts[-1][3] == name:
                parts[-1][1] += h
                parts[-1][2] += mi
            else:
                parts.append([mode, h, mi, name])
        s0, s1 = stops[k], stops[k + 1]
        by = {m: [sum(p[1] for p in parts if p[0] == m), sum(p[2] for p in parts if p[0] == m)]
              for m in ("drive", "ferry", "boat", "flight", "bush plane")}
        how = " + ".join(f"{p[0]}{' (' + p[3] + ')' if p[3] else ''} {p[1]:.1f} h" for p in parts if p[1] >= 0.05)
        rows.append(dict(leg=k + 1, **{"from": s0["name"], "to": s1["name"]},
                         to_state=s1["state"], to_stop=PICK[s1["code"]][1] if s1["code"] in PICK else "Great Kobuk Sand Dunes (fly-in)",
                         how=how,
                         drive_h=round(by["drive"][0], 2), drive_mi=round(by["drive"][1]),
                         ferry_h=round(by["ferry"][0], 2), boat_h=round(by["boat"][0], 2),
                         flight_h=round(by["flight"][0] + by["bush plane"][0], 2),
                         leg_h=round((s1["t"] - s0["t"]) / 3600, 2), total_h=round(s1["t"] / 3600, 1),
                         total_mi=round(D[sv[k + 1]] / 1609.344)))
    with open(OUT / "itinerary.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(OUT / "parks.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["order", "park", "state", "stop", "lat", "lon", "arrive_h"])
        for k, s in enumerate(stops):
            w.writerow([k + 1, s["name"], s["state"], PICK.get(s["code"], ("", "Great Kobuk Sand Dunes (fly-in)"))[1], round(s["lat"], 5),
                        round(s["lon"], 5), round(s["t"] / 3600, 1)])
    pts = [f'<wpt lat="{s["lat"]:.5f}" lon="{s["lon"]:.5f}"><name>{k + 1}. {escape(s["name"])}</name></wpt>'
           for k, s in enumerate(stops)]
    trk = "".join(f'<trkpt lat="{c[1]:.5f}" lon="{c[0]:.5f}"/>' for c in C[::3] + [C[-1]])
    (OUT / "route.gpx").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Optimal Drives (@mapzimus)" '
        'xmlns="http://www.topografix.com/GPX/1/1">\n<metadata><name>Every national park, one trip</name>'
        '<copyright author="OpenStreetMap contributors"/></metadata>\n' + "\n".join(pts) +
        f'\n<trk><name>Every national park</name><trkseg>{trk}</trkseg></trk>\n</gpx>\n', encoding="utf-8")
    (OUT / "README.txt").write_text(
        "Every national park in the 50 states, one trip (Optimal Drives, @mapzimus)\n\n"
        f"61 parks, Everglades to Denali: {t['hours']:.0f} hours, {t['dist'][-1] / 1609.344:,.0f} miles by road and boat.\n"
        "itinerary.csv: one row per leg (how: drive / ferry / boat / flight, hours and miles, running totals)\n"
        "parks.csv: the 61 stops in order with coordinates\n"
        "route.gpx: waypoints + the full track (open in Google Earth, Gaia, OsmAnd...)\n\n"
        "Drive times: OSRM on OpenStreetMap, no traffic, no stops. Boat and bush-plane times are estimates.\n"
        "Map data (c) OpenStreetMap contributors (ODbL).\n", encoding="utf-8")
    print(f"{len(rows)} legs -> {OUT}; check: {rows[0]['how']} | {rows[-1]['how']}")


if __name__ == "__main__":
    main()
