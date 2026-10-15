"""Drive a team's 2026 MLB regular season, ballpark to ballpark (Max, 2026-10-15).

Max: "If you were to drive a team's entire year's schedule, what would that look like? ... the
roads could be symbolized by how many times the route takes you on it."

Schedule: MLB Stats API (cache/team_<id>.json, from probe.py), Final games only. The season
starts and ends at the home park. One stop per stay (a series or a homestand, consecutive
games at one park). Each leg is routed by the engine with OSM node ids, so every road segment
(an OSM node pair, either direction) gets a pass number: 1 the first time the season drives it,
2 the second, ... -> tour.json "pass" (one per vertex, for the segment ending there).

    python experiments/season/build.py --team 111        # Red Sox

Writes cache/<team>/tour.json in the walmart/ tour format (stops, coords, t, dist, mode, legs,
hours, miles, flights [], ferries []) plus pass, for video.py.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
OSRM = "http://127.0.0.1:5002"


def get(url: str):
    with urllib.request.urlopen(url, timeout=300) as r:
        return json.load(r)


def decode(poly: str, prec: float = 1e6) -> list[list[float]]:
    pts, i, lat, lon = [], 0, 0, 0
    while i < len(poly):
        for k in range(2):
            shift = res = 0
            while True:
                b = ord(poly[i]) - 63
                i += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            dlt = ~(res >> 1) if res & 1 else res >> 1
            if k == 0:
                lat += dlt
            else:
                lon += dlt
        pts.append([lon / prec, lat / prec])
    return pts


def stays(team_id: int) -> tuple[str, list[dict]]:
    d = json.load(open(CACHE / f"team_{team_id}.json", encoding="utf-8"))
    games = [g for day in d["dates"] for g in day["games"] if g["status"]["detailedState"] == "Final"]
    teams = {t["id"]: t for t in get(f"https://statsapi.mlb.com/api/v1/teams?sportId=1&season=2026")["teams"]}
    team = teams[team_id]
    out = []
    for g in games:
        v = g["venue"]
        loc = v.get("location", {})
        c = loc.get("defaultCoordinates", {})
        is_home = g["teams"]["home"]["team"]["id"] == team_id
        us, them = (g["teams"]["home"], g["teams"]["away"]) if is_home else (g["teams"]["away"], g["teams"]["home"])
        opp = them["team"]
        res = [us.get("score", 0), them.get("score", 0), bool(us.get("isWinner"))]      # final score, ours first
        if out and out[-1]["name"] == v["name"]:
            out[-1]["games"] += 1
            out[-1]["last"] = g["officialDate"]
            out[-1]["results"].append(res)
            out[-1]["times"].append(g["gameDate"])
            continue
        out.append(dict(name=v["name"], town=f"{loc.get('city', '')}, {loc.get('stateAbbrev', '')}".strip(", "),
                        lon=c["longitude"], lat=c["latitude"], date=g["officialDate"], last=g["officialDate"],
                        games=1, opp=teams.get(opp["id"], {}).get("teamName", opp.get("name", "")),
                        home=is_home, results=[res], times=[g["gameDate"]], region="US",
                        state=loc.get("stateAbbrev", "")))
    w = l = 0
    for x in out:                                  # running record after each stay
        w += sum(1 for r in x["results"] if r[2])
        l += sum(1 for r in x["results"] if not r[2])
        x["record"] = [w, l]
    home = team["venue"]["name"]
    hv = next(s for s in out if s["name"] == home)
    start = dict(hv, date=None, last=None, games=0, opp="", home=True, start=True, results=[], record=[0, 0], times=[])
    end = dict(hv, date=None, last=None, games=0, opp="", home=True, end=True, results=[], record=out[-1]["record"], times=[])
    seq = ([start] if out[0]["name"] != home else []) + out + ([end] if out[-1]["name"] != home else [])
    return team["name"], seq


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", type=int, default=111)
    args = ap.parse_args()
    name, st = stays(args.team)
    coords, t, dist, mode, passes, legs = [[st[0]["lon"], st[0]["lat"]]], [0.0], [0.0], ["road"], [0], []
    seen = collections.Counter()
    clock = metres = 0.0
    st[0]["t"] = 0.0
    for a, b in zip(st, st[1:]):
        r = get(f"{OSRM}/route/v1/driving/{a['lon']},{a['lat']};{b['lon']},{b['lat']}"
                "?overview=full&geometries=polyline6&annotations=nodes,duration,distance")["routes"][0]
        leg = r["legs"][0]
        pts = decode(r["geometry"])
        nodes = leg["annotation"]["nodes"]
        durs, dists = leg["annotation"]["duration"], leg["annotation"]["distance"]
        n = min(len(pts) - 1, len(durs))
        for v in range(n):
            key = tuple(sorted((nodes[v], nodes[v + 1]))) if v + 1 < len(nodes) else (nodes[v], -1)
            seen[key] += 1
            clock += durs[v]
            metres += dists[v]
            coords.append(pts[v + 1])
            t.append(clock)
            dist.append(metres)
            mode.append("road")
            passes.append(seen[key])
        b["t"] = clock
        legs.append(dict(frm=a["name"], to=b["name"], h=leg["duration"] / 3600, mi=leg["distance"] / 1609.344,
                         ferry=False, alaska=False))
    # A fan driving nonstop, leaving 3 h after the last first pitch of a stay: games missed at the next one.
    for a, b, leg in zip(st, st[1:], legs):
        b["missed"], b["late_min"] = 0, 0
        if not a.get("times") or not b.get("times"):
            continue
        leave = dt.datetime.fromisoformat(a["times"][-1].replace("Z", "+00:00")) + dt.timedelta(hours=3)
        arrive = leave + dt.timedelta(hours=leg["h"])
        first = [dt.datetime.fromisoformat(x.replace("Z", "+00:00")) for x in b["times"]]
        b["missed"] = sum(1 for x in first if x < arrive)
        if b["missed"]:
            b["late_min"] = round((arrive - first[0]).total_seconds() / 60)
    hist = collections.Counter(passes[1:])
    tour = dict(team=name, stops=st, coords=coords, t=t, dist=dist, mode=mode, pass_=passes, flights=[], ferries=[],
                legs=legs, hours=clock / 3600, fly_hours=0.0, miles=metres / 1609.344, flown_miles=0.0)
    out = CACHE / str(args.team)
    out.mkdir(parents=True, exist_ok=True)
    json.dump(tour, open(out / "tour.json", "w", encoding="utf-8"))
    total = sum(hist.values())
    print(f"{name}: {len(st)} stops, {len(legs)} legs, {clock / 3600:,.0f} h, {metres / 1609.344:,.0f} mi; "
          f"vertices by pass number: " + ", ".join(f"{k}x {v / total:.1%}" for k, v in sorted(hist.items())[:10])
          + f"; max {max(hist)}")
    # the most-driven stretches
    worst = sorted(((p, i) for i, p in enumerate(passes)), reverse=True)[:1]
    i = worst[0][1]
    print("missed:", [(x["name"], x["missed"], x["late_min"]) for x in st if x.get("missed")])
    print(f"most-driven point: {passes[i]}x at {coords[i][1]:.4f},{coords[i][0]:.4f}")


if __name__ == "__main__":
    main()
