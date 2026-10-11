"""Flights to road networks you can't drive to (Hawaii, Puerto Rico).

Timing model (all hours from leaving the origin):
    drive origin -> home airport
    + AIRPORT_BEFORE           arrive early, security, boarding
    + flight                   great-circle miles / CRUISE_MPH + TAXI_CLIMB
    + AIRPORT_AFTER            deplane, rental car             -> "ready"
Connections (Honolulu -> neighbor islands) leave CONNECTION after landing.

There are no inter-island car ferries in Hawaii (the Superferry stopped in
2009), so the neighbor islands are reached by these short hops.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict

CRUISE_MPH = 500
TAXI_CLIMB = 0.5
AIRPORT_BEFORE = 1.5
AIRPORT_AFTER = 0.5
CONNECTION = 1.5

AIRPORTS = {
    "BOS": ("Boston Logan", -71.0096, 42.3656),
    "HNL": ("Honolulu", -157.9225, 21.3187),
    "OGG": ("Kahului, Maui", -156.4305, 20.8986),
    "KOA": ("Kona", -156.0456, 19.7388),
    "ITO": ("Hilo", -155.0485, 19.7203),
    "LIH": ("Lihue, Kauai", -159.3390, 21.9760),
    "SJU": ("San Juan", -66.0018, 18.4394),
}

# Island road networks each arrival airport serves (west, south, east, north).
HAWAII = (-160.6, 18.8, -154.7, 22.4)
PUERTO_RICO = (-67.4, 17.8, -65.2, 18.6)
SERVES = {"HNL": HAWAII, "OGG": HAWAII, "KOA": HAWAII, "ITO": HAWAII,
          "LIH": HAWAII, "SJU": PUERTO_RICO}

# (from, to); a "from" other than the home airport is a connection.
ROUTES = [("BOS", "HNL"), ("BOS", "SJU"),
          ("HNL", "OGG"), ("HNL", "KOA"), ("HNL", "ITO"), ("HNL", "LIH")]


@dataclass
class Leg:
    src: str
    dst: str
    depart: float     # wheels-up, hours from leaving the origin
    arrive: float     # wheels-down
    ready: float      # in the rental car, starts driving
    miles: float


def gc_miles(a: str, b: str) -> float:
    _, lon1, lat1 = AIRPORTS[a]
    _, lon2, lat2 = AIRPORTS[b]
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    c = math.acos(min(1.0, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)))
    return 3958.8 * c


def plan(hours_to_home_airport: float, home: str = "BOS") -> list[Leg]:
    """Schedule every leg in ROUTES, connections after their inbound leg."""
    legs: dict[str, Leg] = {}
    for src, dst in ROUTES:
        if src == home:
            depart = hours_to_home_airport + AIRPORT_BEFORE
        else:
            depart = legs[src].arrive + CONNECTION
        miles = gc_miles(src, dst)
        arrive = depart + miles / CRUISE_MPH + TAXI_CLIMB
        legs[dst] = Leg(src, dst, depart, arrive, arrive + AIRPORT_AFTER, miles)
    return list(legs.values())


def great_circle(a: str, b: str, n: int = 64) -> list[tuple[float, float]]:
    """(lon, lat) points along the great circle from airport a to b."""
    _, lon1, lat1 = AIRPORTS[a]
    _, lon2, lat2 = AIRPORTS[b]
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d = gc_miles(a, b) / 3958.8                     # central angle, radians
    pts = []
    for i in range(n + 1):
        f = i / n
        A, B = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
        x = A * math.cos(p1) * math.cos(l1) + B * math.cos(p2) * math.cos(l2)
        y = A * math.cos(p1) * math.sin(l1) + B * math.cos(p2) * math.sin(l2)
        z = A * math.sin(p1) + B * math.sin(p2)
        pts.append((math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))))
    return pts


def legs_json(legs: list[Leg]) -> list[dict]:
    return [{**asdict(l), "path": great_circle(l.src, l.dst)} for l in legs]
