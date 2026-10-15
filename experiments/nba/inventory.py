"""Every NBA arena (30 teams, 28 cities; viewer request, 2026-10-14).

Arena coordinates, 2026-27 season; labels use team and city, not arena names
(several were renamed in 2025). Division for the chips. Checked by snapping each
arena to the road network (OSRM /nearest): a big snap distance means a bad point.

    python experiments/nba/inventory.py

Writes cache/stores.json (name, lon, lat, town, division) for walmart/build.py --cache.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OSRM = "http://127.0.0.1:5002"
# team, city, division, lat, lon (arena)
ARENAS = [
    ("Atlanta Hawks", "Atlanta", "Southeast", 33.7573, -84.3963),
    ("Boston Celtics", "Boston", "Atlantic", 42.3662, -71.0621),
    ("Brooklyn Nets", "Brooklyn", "Atlantic", 40.6826, -73.9754),
    ("Charlotte Hornets", "Charlotte", "Southeast", 35.2251, -80.8392),
    ("Chicago Bulls", "Chicago", "Central", 41.8807, -87.6742),
    ("Cleveland Cavaliers", "Cleveland", "Central", 41.4965, -81.6882),
    ("Dallas Mavericks", "Dallas", "Southwest", 32.7905, -96.8103),
    ("Denver Nuggets", "Denver", "Northwest", 39.7487, -105.0077),
    ("Detroit Pistons", "Detroit", "Central", 42.3411, -83.0553),
    ("Golden State Warriors", "San Francisco", "Pacific", 37.7680, -122.3877),
    ("Houston Rockets", "Houston", "Southwest", 29.7508, -95.3621),
    ("Indiana Pacers", "Indianapolis", "Central", 39.7640, -86.1555),
    ("LA Clippers", "Inglewood", "Pacific", 33.9450, -118.3430),
    ("Los Angeles Lakers", "Los Angeles", "Pacific", 34.0430, -118.2673),
    ("Memphis Grizzlies", "Memphis", "Southwest", 35.1382, -90.0506),
    ("Miami Heat", "Miami", "Southeast", 25.7814, -80.1870),
    ("Milwaukee Bucks", "Milwaukee", "Central", 43.0451, -87.9172),
    ("Minnesota Timberwolves", "Minneapolis", "Northwest", 44.9795, -93.2761),
    ("New Orleans Pelicans", "New Orleans", "Southwest", 29.9490, -90.0821),
    ("New York Knicks", "New York", "Atlantic", 40.7505, -73.9934),
    ("Oklahoma City Thunder", "Oklahoma City", "Northwest", 35.4634, -97.5151),
    ("Orlando Magic", "Orlando", "Southeast", 28.5392, -81.3839),
    ("Philadelphia 76ers", "Philadelphia", "Atlantic", 39.9012, -75.1720),
    ("Phoenix Suns", "Phoenix", "Pacific", 33.4457, -112.0712),
    ("Portland Trail Blazers", "Portland", "Northwest", 45.5316, -122.6668),
    ("Sacramento Kings", "Sacramento", "Pacific", 38.5802, -121.4997),
    ("San Antonio Spurs", "San Antonio", "Southwest", 29.4270, -98.4375),
    ("Toronto Raptors", "Toronto", "Atlantic", 43.6435, -79.3791),
    ("Utah Jazz", "Salt Lake City", "Northwest", 40.7683, -111.9011),
    ("Washington Wizards", "Washington", "Southeast", 38.8981, -77.0209),
]


def main() -> None:
    stores = []
    for team, city, div, lat, lon in ARENAS:
        with urllib.request.urlopen(f"{OSRM}/nearest/v1/driving/{lon},{lat}", timeout=30) as r:
            snap = json.load(r)["waypoints"][0]["distance"]
        flag = "  <-- check" if snap > 400 else ""
        print(f"  {team:24s} {city:15s} snap {snap:5.0f} m{flag}")
        stores.append(dict(name=team, town=city, division=div, lon=lon, lat=lat))
    json.dump(stores, open(HERE / "cache" / "stores.json", "w", encoding="utf-8"), indent=0)
    print(f"{len(stores)} arenas, {len({s['town'] for s in stores})} cities")


if __name__ == "__main__":
    main()
