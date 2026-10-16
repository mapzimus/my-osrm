"""Every NHL arena (32 teams, Canada included; viewer request from mmmeltzer, 2026-10-16: "Could you
do NHL next?" after the NFL video; Max: a drive to every arena, Optimal Drives, "include Canada
definitely"). Copies nba/inventory.py.

Arena coordinates, 2026-27 season; divisions for the chips. Checked by snapping each arena to the
road network (OSRM /nearest, the na-ferry engine covers Canada): a big snap distance means a bad point.

    python experiments/nhl/inventory.py

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
    ("Boston Bruins", "Boston", "Atlantic", 42.3662, -71.0621),
    ("Buffalo Sabres", "Buffalo", "Atlantic", 42.8750, -78.8764),
    ("Detroit Red Wings", "Detroit", "Atlantic", 42.3411, -83.0553),
    ("Florida Panthers", "Sunrise", "Atlantic", 26.1584, -80.3256),
    ("Montreal Canadiens", "Montreal", "Atlantic", 45.4961, -73.5693),
    ("Ottawa Senators", "Ottawa", "Atlantic", 45.2969, -75.9271),
    ("Tampa Bay Lightning", "Tampa", "Atlantic", 27.9427, -82.4519),
    ("Toronto Maple Leafs", "Toronto", "Atlantic", 43.6435, -79.3791),
    ("Carolina Hurricanes", "Raleigh", "Metropolitan", 35.8033, -78.7219),
    ("Columbus Blue Jackets", "Columbus", "Metropolitan", 39.9693, -83.0061),
    ("New Jersey Devils", "Newark", "Metropolitan", 40.7334, -74.1711),
    ("New York Islanders", "Elmont", "Metropolitan", 40.7120, -73.7254),
    ("New York Rangers", "New York", "Metropolitan", 40.7505, -73.9934),
    ("Philadelphia Flyers", "Philadelphia", "Metropolitan", 39.9012, -75.1720),
    ("Pittsburgh Penguins", "Pittsburgh", "Metropolitan", 40.4395, -79.9892),
    ("Washington Capitals", "Washington", "Metropolitan", 38.8981, -77.0209),
    ("Chicago Blackhawks", "Chicago", "Central", 41.8807, -87.6742),
    ("Colorado Avalanche", "Denver", "Central", 39.7487, -105.0077),
    ("Dallas Stars", "Dallas", "Central", 32.7905, -96.8103),
    ("Minnesota Wild", "St. Paul", "Central", 44.9448, -93.1010),
    ("Nashville Predators", "Nashville", "Central", 36.1592, -86.7785),
    ("St. Louis Blues", "St. Louis", "Central", 38.6268, -90.2026),
    ("Utah Mammoth", "Salt Lake City", "Central", 40.7683, -111.9011),
    ("Winnipeg Jets", "Winnipeg", "Central", 49.8928, -97.1436),
    ("Anaheim Ducks", "Anaheim", "Pacific", 33.8078, -117.8765),
    ("Calgary Flames", "Calgary", "Pacific", 51.0375, -114.0519),
    ("Edmonton Oilers", "Edmonton", "Pacific", 53.5469, -113.4979),
    ("Los Angeles Kings", "Los Angeles", "Pacific", 34.0430, -118.2673),
    ("San Jose Sharks", "San Jose", "Pacific", 37.3327, -121.9010),
    ("Seattle Kraken", "Seattle", "Pacific", 47.6221, -122.3540),
    ("Vancouver Canucks", "Vancouver", "Pacific", 49.2778, -123.1088),
    ("Vegas Golden Knights", "Las Vegas", "Pacific", 36.1029, -115.1784),
]
CANADA = {"Montreal", "Ottawa", "Toronto", "Winnipeg", "Calgary", "Edmonton", "Vancouver"}


def main() -> None:
    stores = []
    for team, city, div, lat, lon in ARENAS:
        with urllib.request.urlopen(f"{OSRM}/nearest/v1/driving/{lon},{lat}", timeout=30) as r:
            snap = json.load(r)["waypoints"][0]["distance"]
        flag = "  <-- check" if snap > 400 else ""
        print(f"  {team:24s} {city:15s} snap {snap:5.0f} m{flag}")
        stores.append(dict(name=team, town=city, division=div, lon=lon, lat=lat, canada=city in CANADA))
    json.dump(stores, open(HERE / "cache" / "stores.json", "w", encoding="utf-8"), indent=0)
    print(f"{len(stores)} arenas, {sum(s['canada'] for s in stores)} in Canada")


if __name__ == "__main__":
    main()
