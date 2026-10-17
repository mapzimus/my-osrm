"""Every state, province and territory capital in the US, Canada and Mexico,
as a building (Max, 2026-10-14: the capitol buildings; then "add Mexican and
Canadian state/province capitals too"; no Puerto Rico, no DC).

  US: the state capitol (Wikipedia's name for it).
  Canada: the legislature building.
  Mexico: the Palacio de Gobierno (seat of the state government) nearest the
  city centre; Mexico City: the Antiguo Palacio del Ayuntamiento.

Coordinates from Wikidata (P625). A building only counts within MAX_KM of
its capital city, so a namesake elsewhere can't slip in. A Mexican capital
with no Palacio de Gobierno on Wikidata falls back to the city point and is
listed.

    python experiments/capitals/inventory.py

Writes cache/capitols.json: [{name, division, city, country, lon, lat, region, qid}].
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
UA = {"User-Agent": "my-osrm capitals (mapzimus)"}
MAX_KM = 15
PALACIO_KM = 3
# (country, division, capital city, building or None for the Palacio de Gobierno search)
DIVISIONS = [
    ("US", "Alabama", "Montgomery", "Alabama State Capitol"), ("US", "Alaska", "Juneau", "Alaska State Capitol"),
    ("US", "Arizona", "Phoenix", "Arizona State Capitol"), ("US", "Arkansas", "Little Rock", "Arkansas State Capitol"),
    ("US", "California", "Sacramento", "California State Capitol"),
    ("US", "Colorado", "Denver", "Colorado State Capitol"),
    ("US", "Connecticut", "Hartford", "Connecticut State Capitol"), ("US", "Delaware", "Dover", "Delaware Legislative Hall"),
    ("US", "Florida", "Tallahassee", "Florida State Capitol"), ("US", "Georgia", "Atlanta", "Georgia State Capitol"),
    ("US", "Hawaii", "Honolulu", "Hawaii State Capitol"), ("US", "Idaho", "Boise", "Idaho State Capitol"),
    ("US", "Illinois", "Springfield", "Illinois State Capitol"),
    ("US", "Indiana", "Indianapolis", "Indiana Statehouse"), ("US", "Iowa", "Des Moines", "Iowa State Capitol"),
    ("US", "Kansas", "Topeka", "Kansas State Capitol"), ("US", "Kentucky", "Frankfort", "Kentucky State Capitol"),
    ("US", "Louisiana", "Baton Rouge", "Louisiana State Capitol"), ("US", "Maine", "Augusta", "Maine State House"),
    ("US", "Maryland", "Annapolis", "Maryland State House"),
    ("US", "Massachusetts", "Boston", "Massachusetts State House"),
    ("US", "Michigan", "Lansing", "Michigan State Capitol"),
    ("US", "Minnesota", "Saint Paul", "Minnesota State Capitol"),
    ("US", "Mississippi", "Jackson", "Mississippi State Capitol"),
    ("US", "Missouri", "Jefferson City", "Missouri State Capitol"),
    ("US", "Montana", "Helena", "Montana State Capitol"), ("US", "Nebraska", "Lincoln", "Nebraska State Capitol"),
    ("US", "Nevada", "Carson City", "Nevada State Capitol"),
    ("US", "New Hampshire", "Concord", "New Hampshire State House"),
    ("US", "New Jersey", "Trenton", "New Jersey State House"),
    ("US", "New Mexico", "Santa Fe", "New Mexico State Capitol"),
    ("US", "New York", "Albany", "New York State Capitol"),
    ("US", "North Carolina", "Raleigh", "North Carolina State Capitol"),
    ("US", "North Dakota", "Bismarck", "North Dakota State Capitol"), ("US", "Ohio", "Columbus", "Ohio Statehouse"),
    ("US", "Oklahoma", "Oklahoma City", "Oklahoma State Capitol"), ("US", "Oregon", "Salem", "Oregon State Capitol"),
    ("US", "Pennsylvania", "Harrisburg", "Pennsylvania State Capitol"),
    ("US", "Rhode Island", "Providence", "Rhode Island State House"),
    ("US", "South Carolina", "Columbia", "South Carolina State House"),
    ("US", "South Dakota", "Pierre", "South Dakota State Capitol"),
    ("US", "Tennessee", "Nashville", "Tennessee State Capitol"), ("US", "Texas", "Austin", "Texas State Capitol"),
    ("US", "Utah", "Salt Lake City", "Utah State Capitol"), ("US", "Vermont", "Montpelier", "Vermont State House"),
    ("US", "Virginia", "Richmond", "Virginia State Capitol"),
    ("US", "Washington", "Olympia", "Washington State Capitol"),
    ("US", "West Virginia", "Charleston", "West Virginia State Capitol"),
    ("US", "Wisconsin", "Madison", "Wisconsin State Capitol"), ("US", "Wyoming", "Cheyenne", "Wyoming State Capitol"),
    ("CA", "Alberta", "Edmonton", "Alberta Legislature Building"),
    ("CA", "British Columbia", "Victoria", "British Columbia Parliament Buildings"),
    ("CA", "Manitoba", "Winnipeg", "Manitoba Legislative Building"),
    ("CA", "New Brunswick", "Fredericton", "New Brunswick Legislative Building"),
    ("CA", "Newfoundland and Labrador", "St. John's", "Confederation Building"),
    ("CA", "Nova Scotia", "Halifax", "Province House"),
    ("CA", "Ontario", "Toronto", "Ontario Legislative Building"),
    ("CA", "Prince Edward Island", "Charlottetown", "Province House"),
    ("CA", "Quebec", "Quebec City", "Parliament Building of Quebec"),
    ("CA", "Saskatchewan", "Regina", "Saskatchewan Legislative Building"),
    ("CA", "Yukon", "Whitehorse", "Yukon Legislative Building"),
    ("CA", "Northwest Territories", "Yellowknife", "Northwest Territories Legislative Building"),
    ("CA", "Nunavut", "Iqaluit", "Legislative Building of Nunavut"),
    *[("MX", d, c, None) for d, c in (
        ("Aguascalientes", "Aguascalientes"), ("Baja California", "Mexicali"), ("Baja California Sur", "La Paz"),
        ("Campeche", "Campeche"), ("Chiapas", "Tuxtla Gutiérrez"), ("Chihuahua", "Chihuahua"),
        ("Coahuila", "Saltillo"), ("Colima", "Colima"), ("Durango", "Durango"), ("Guanajuato", "Guanajuato"),
        ("Guerrero", "Chilpancingo"), ("Hidalgo", "Pachuca"), ("Jalisco", "Guadalajara"),
        ("Michoacán", "Morelia"), ("Morelos", "Cuernavaca"), ("Nayarit", "Tepic"), ("Nuevo León", "Monterrey"),
        ("Oaxaca", "Oaxaca"), ("Puebla", "Puebla"), ("Querétaro", "Querétaro"), ("Quintana Roo", "Chetumal"),
        ("San Luis Potosí", "San Luis Potosí"), ("Sinaloa", "Culiacán"), ("Sonora", "Hermosillo"),
        ("State of Mexico", "Toluca"), ("Tabasco", "Villahermosa"), ("Tamaulipas", "Ciudad Victoria"),
        ("Tlaxcala", "Tlaxcala"), ("Veracruz", "Xalapa"), ("Yucatán", "Mérida"), ("Zacatecas", "Zacatecas"))],
    ("MX", "Mexico City", "Mexico City", "Antiguo Palacio del Ayuntamiento"),
]
CLASSES = "wd:Q35657 wd:Q11828004 wd:Q9357527 wd:Q15149663"
# Capital cities Wikidata gives no point for (via P36 of the division).
CITY_POINTS = {"Alaska": (-134.4197, 58.3019), "New Brunswick": (-66.6431, 45.9636),
               "Mexico City": (-99.1332, 19.4326)}


def km(a, b) -> float:
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def pick(cands: list[tuple[str, tuple[float, float]]], city: tuple[float, float], limit: float = MAX_KM):
    """The candidate nearest the capital city, if within limit km."""
    near = [c for c in cands if km(c[1], city) <= limit]
    return min(near, key=lambda c: km(c[1], city)) if near else None


def sparql(q: str, tries: int = 4) -> list[dict]:
    """Wikidata's query service; it answers 502/429 now and then, so retry with backoff."""
    req = urllib.request.Request("https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q),
                                 headers=UA)
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(req, timeout=120))["results"]["bindings"]
        except urllib.error.HTTPError as e:
            if k == tries - 1 or e.code not in (429, 500, 502, 503, 504):
                raise
            time.sleep(5 * 2 ** k)


def point(wkt: str) -> tuple[float, float]:
    lon, lat = wkt.removeprefix("Point(").rstrip(")").split()
    return float(lon), float(lat)


def main() -> None:
    cities = dict(CITY_POINTS)
    for r in sparql(f"""SELECT ?sL ?c WHERE {{ VALUES ?cls {{ {CLASSES} }} ?s wdt:P31 ?cls; wdt:P36 ?city.
                       ?city wdt:P625 ?c. ?s rdfs:label ?sL. FILTER(lang(?sL)='en') }}"""):
        cities.setdefault(r["sL"]["value"], point(r["c"]["value"]))
    names = {b for *_, b in DIVISIONS if b}
    labels = " ".join(f'"{b}"@en' for b in names)
    hits: dict[str, list] = {}
    for r in sparql(f"""SELECT ?i ?l ?c WHERE {{ VALUES ?l {{ {labels} }} ?i rdfs:label ?l; wdt:P625 ?c. }}"""):
        hits.setdefault(r["l"]["value"], []).append((r["i"]["value"].rsplit("/", 1)[1], point(r["c"]["value"])))
    out, missing, fallback = [], [], []
    for country, division, city, building in DIVISIONS:
        cxy = cities[division]
        if building:
            got, name = pick(hits.get(building, []), cxy), building
        else:
            rows = sparql(f"""SELECT ?i ?l ?c WHERE {{
                SERVICE wikibase:around {{ ?i wdt:P625 ?c. bd:serviceParam wikibase:center "Point({cxy[0]} {cxy[1]})"^^geo:wktLiteral;
                                          wikibase:radius "{PALACIO_KM}". }}
                ?i rdfs:label ?l. FILTER(lang(?l) in ('es','en') && CONTAINS(LCASE(?l), "palacio de gobierno")) }}""")
            cands = {r["i"]["value"].rsplit("/", 1)[1]: (r["l"]["value"], point(r["c"]["value"])) for r in rows}
            got = pick([(q, v[1]) for q, v in cands.items()], cxy, PALACIO_KM)
            name = "Palacio de Gobierno"            # one name for all (Wikidata has museum and mural items too)
            if got is None:                         # the city point: its main square, where the palacio stands
                got = ("", cxy)
                fallback.append(division)
        if got is None:
            missing.append(building)
            continue
        qid, (lon, lat) = got
        out.append(dict(name=f"{name}, {city}", building=name, division=division, city=city, country=country,
                        lon=lon, lat=lat, region={"US": "us", "CA": "canada", "MX": "mexico"}[country], qid=qid,
                        approx=not qid))
    if missing:
        raise SystemExit(f"not found near the capital city: {missing}")
    json.dump(out, open(CACHE / "capitols.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"{len(out)} capitals; city-centre fallback: {fallback or 'none'}")


if __name__ == "__main__":
    main()
