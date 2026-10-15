"""Data probe (2026-10-15): drive every MLB team's 2026 regular season, venue to venue.

For each team: the order of ballparks it played in (MLB Stats API, statsapi.mlb.com, free,
no key), starting and ending at its home park. Legs = changes of venue. Drive time and
miles per leg from the engine's table (one call for all parks). Prints teams by miles,
plus each team's most repeated leg (the road the season wears in).

    python experiments/season/probe.py
"""

from __future__ import annotations

import collections
import json
import urllib.request
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "cache"
OSRM = "http://127.0.0.1:5002"
API = "https://statsapi.mlb.com/api/v1"


def get(url: str):
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r)


def main() -> None:
    teams = get(f"{API}/teams?sportId=1&season=2026")["teams"]
    venues, seasons = {}, {}
    for t in teams:
        path = CACHE / f"team_{t['id']}.json"
        if not path.exists():
            path.write_text(json.dumps(get(f"{API}/schedule?sportId=1&teamId={t['id']}&season=2026&gameType=R"
                                           "&hydrate=venue(location)")))
        d = json.load(open(path, encoding="utf-8"))
        games = [g for day in d["dates"] for g in day["games"] if g["status"]["detailedState"] == "Final"]
        home = t["venue"]["name"]
        seq = [home]
        for g in games:
            v = g["venue"]
            c = v.get("location", {}).get("defaultCoordinates", {})
            venues[v["name"]] = (c.get("longitude"), c.get("latitude"))
            if seq[-1] != v["name"]:
                seq.append(v["name"])
        if seq[-1] != home:
            seq.append(home)
        seasons[t["name"]] = dict(home=home, seq=seq, games=len(games))
    names = sorted(venues)
    bad = [n for n in names if venues[n][0] is None]
    names = [n for n in names if n not in bad]
    coords = ";".join(f"{venues[n][0]},{venues[n][1]}" for n in names)
    tab = get(f"{OSRM}/table/v1/driving/{coords}?annotations=duration,distance")
    ix = {n: i for i, n in enumerate(names)}
    rows = []
    for team, s in seasons.items():
        h = mi = 0.0
        legs = collections.Counter()
        nores = []
        for a, b in zip(s["seq"], s["seq"][1:]):
            if a in bad or b in bad:
                nores.append((a, b))
                continue
            dur, dist = tab["durations"][ix[a]][ix[b]], tab["distances"][ix[a]][ix[b]]
            if dur is None:
                nores.append((a, b))
                continue
            h += dur / 3600
            mi += dist / 1609.344
            legs[tuple(sorted((a, b)))] += 1
        top, n = legs.most_common(1)[0]
        rows.append((mi, team, h, len(s["seq"]) - 1, s["games"], n, top, nores))
    rows.sort(reverse=True)
    print(f"{len(names)} ballparks with coordinates; no coordinates: {bad}")
    for mi, team, h, legs, g, n, top, nores in rows:
        print(f"{team:24s} {mi:8,.0f} mi {h:6.0f} h  {legs:2d} legs  {g} games  most repeated: {n}x {top[0][:18]} - "
              f"{top[1][:18]}" + (f"  NO ROAD: {nores}" if nores else ""))
    json.dump({t: s["seq"] for t, s in seasons.items()}, open(CACHE / "sequences.json", "w"), indent=0)
    json.dump(venues, open(CACHE / "venues.json", "w"), indent=0)


if __name__ == "__main__":
    main()
