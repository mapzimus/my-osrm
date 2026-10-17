"""Every big-5 venue (viewer request, Rojbew on TikTok, 2026-10-17: "What about a trip to every
stadium in the big 5 sports the MLS NHL NBA NFL and MLB"). One stop per building (Max), each
showing the logos of every team that plays there.

Data probe (2026-10-17, files in cache/check/):
- Teams and home venues from where each team actually plays its 2026 home games: ESPN team
  schedules for NBA (2026-27), NHL (2026-27), NFL (2026) and MLS (2026); MLB's statsapi schedule
  (2026). The busiest non-neutral venue is the home (NYCFC: Yankee Stadium 10, Citi Field 4).
  ESPN's "franchise venue" field is stale (Warriors at Oracle Arena), so it is not used.
- 154 teams, 135 buildings once shared ones are one (matched by name, ALIAS: ESPN and MLB
  spell some differently).
- Positions: OpenStreetMap (Nominatim, "<venue>, <city>"), checked against our old hand-typed
  NBA/NHL/NFL points and MLB's: 130 within 0.22 km. The Bills moved to the new Highmark Stadium
  in Sept 2026 (old point was 0.43 km off); Inter Miami to Nu Stadium. FIX: four OSM didn't find
  by the schedule's name. All 135 snap to the engine's roads (Arrowhead, Dodger Stadium ~430 m:
  parking lots).
- Logos: ESPN's team logos (the "dark" version where there is one, for the dark map).

    python experiments/big5/inventory.py

Writes cache/stores.json (name, town, state, lon, lat, teams) for walmart/build.py --cache and
downloads assets/logos/<league>_<abbr>.png.
"""

from __future__ import annotations

import collections
import json
import re
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE, CHECK, LOGOS = HERE / "cache", HERE / "cache" / "check", HERE / "assets" / "logos"
ESPN = {"NBA": "basketball/nba", "NHL": "hockey/nhl", "NFL": "football/nfl", "MLS": "soccer/usa.1",
        "MLB": "baseball/mlb"}
ALIAS = {"Stade Saputo": "Saputo Stadium", "Reliant Stadium": "NRG Stadium",
         "Arrowhead Stadium": "GEHA Field at Arrowhead Stadium", "Toyota Center (Houston)": "Toyota Center",
         "crypto.com Arena": "Crypto.com Arena", "GEODIS Park": "Geodis Park"}
FIX = {"Q2 Stadium": (-97.7198, 30.3882), "Toyota Stadium": (-96.8354, 33.1542),       # Wikidata / OSM
       "GEHA Field at Arrowhead Stadium": (-94.4840, 39.0489), "UNIQLO Field at Dodger Stadium": (-118.2398, 34.0736)}
LEAGUE_ORDER = ["NFL", "MLB", "NBA", "NHL", "MLS"]
PROVINCES = {"ON", "QC", "BC", "AB", "MB"}
# ESPN's MLS venues spell states out; a few have no state at all (CITY).
STATE = {"Ontario": "ON", "Quebec": "QC", "British Columbia": "BC", "Alberta": "AB", "Manitoba": "MB",
         "California": "CA", "Colorado": "CO", "District of Columbia": "DC", "Florida": "FL", "Kansas": "KS",
         "Minnesota": "MN", "Missouri": "MO", "New Jersey": "NJ", "Ohio": "OH", "Oregon": "OR",
         "Pennsylvania": "PA", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Georgia": "GA",
         "North Carolina": "NC", "Illinois": "IL", "Massachusetts": "MA", "Washington": "WA"}
CITY = {"Saputo Stadium": ("Montreal", "QC"), "Yankee Stadium": ("New York", "NY"), "BMO Field": ("Toronto", "ON"),
        "Toyota Stadium": ("Frisco", "TX"), "BC Place": ("Vancouver", "BC")}


def get(u: str):
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("é", "e"))


def main() -> None:
    venues = json.load(open(CHECK / "venues_by_name.json", encoding="utf-8"))
    osm = json.load(open(CHECK / "osm_check.json", encoding="utf-8"))
    LOGOS.mkdir(parents=True, exist_ok=True)
    espn = {}                                        # (league, normalized name) -> team record
    for lg, path in ESPN.items():
        for t in get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams")["sports"][0]["leagues"][0]["teams"]:
            t = t["team"]
            espn[(lg, norm(t["displayName"]))] = t
    stores = []
    for name, teams in venues.items():
        lon, lat = FIX.get(name) or osm[name]["osm"][0][:2]
        city = teams[0][2]
        town, _, st = city.partition(", ")
        st = STATE.get(st, st)
        if name in CITY:
            town, st = CITY[name]
        recs = []
        for lg, team, _ in teams:
            t = espn.get((lg, norm(team))) or next(v for (l, k), v in espn.items() if l == lg and norm(team) in k)
            logos = t.get("logos", [])
            href = next((x["href"] for x in logos if "dark" in x.get("rel", [])), logos[0]["href"])
            f = LOGOS / f"{lg.lower()}_{t['abbreviation'].lower()}.png"
            if not f.exists():
                urllib.request.urlretrieve(href, f)
            recs.append(dict(league=lg, team=t["displayName"], abbr=t["abbreviation"], logo=f.name))
        recs.sort(key=lambda r: LEAGUE_ORDER.index(r["league"]))
        stores.append(dict(name=name, town=town, state=st,
                           ref=name, lon=lon, lat=lat, canada=st in PROVINCES, teams=recs))
    stores.sort(key=lambda s: (s["state"], s["town"], s["name"]))
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0, ensure_ascii=False)
    n_teams = sum(len(s["teams"]) for s in stores)
    by = collections.Counter(r["league"] for s in stores for r in s["teams"])
    print(f"{len(stores)} buildings, {n_teams} teams {dict(by)}, {sum(s['canada'] for s in stores)} in Canada")
    odd = [(s["name"], s["town"], s["state"]) for s in stores if len(s["state"]) != 2]
    print("odd states:", odd)


if __name__ == "__main__":
    main()
