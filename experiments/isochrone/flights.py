"""Flights to road networks you can't drive to (Hawaii, Puerto Rico).

Timing model (all hours from leaving the origin):
    drive origin -> home airport
    + AIRPORT_BEFORE           arrive early, security, boarding
    + flight                   great-circle miles / CRUISE_MPH + TAXI_CLIMB
    + AIRPORT_AFTER            deplane, rental car             -> "ready"
Connections (Honolulu -> neighbor islands) leave CONNECTION after landing.

There are no inter-island car ferries in Hawaii (the Superferry stopped in
2009), so the neighbor islands are reached by these short hops. Every
Caribbean island connects through Miami; car ferries between islands
(Trinidad-Tobago, Bahamas, St Kitts-Nevis...) come from the routing engine.
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
    "MIA": ("Miami", -80.2906, 25.7959),
    "BDA": ("Bermuda", -64.6787, 32.3640),
    # Caribbean (main airport per island; two or more on the big islands)
    "HAV": ("Havana", -82.4091, 22.9892),
    "HOG": ("Holguin", -76.3151, 20.7856),
    "MBJ": ("Montego Bay", -77.9134, 18.5037),
    "KIN": ("Kingston", -76.7875, 17.9357),
    "SDQ": ("Santo Domingo", -69.6689, 18.4297),
    "PUJ": ("Punta Cana", -68.3634, 18.5674),
    "STI": ("Santiago DR", -70.6047, 19.4061),
    "PAP": ("Port-au-Prince", -72.2925, 18.5800),
    "NAS": ("Nassau", -77.4662, 25.0390),
    "FPO": ("Freeport", -78.6956, 26.5587),
    "MHH": ("Marsh Harbour", -77.0835, 26.5114),
    "ELH": ("North Eleuthera", -76.6835, 25.4749),
    "GGT": ("Great Exuma", -75.8780, 23.5626),
    "GCM": ("Grand Cayman", -81.3577, 19.2928),
    "PLS": ("Providenciales", -72.2659, 21.7736),
    "STT": ("St. Thomas", -64.9734, 18.3373),
    "STX": ("St. Croix", -64.7986, 17.7019),
    "EIS": ("Tortola", -64.5430, 18.4448),
    "AXA": ("Anguilla", -63.0551, 18.2048),
    "SXM": ("St. Maarten", -63.1089, 18.0410),
    "SKB": ("St. Kitts", -62.7187, 17.3112),
    "ANU": ("Antigua", -61.7926, 17.1367),
    "PTP": ("Guadeloupe", -61.5318, 16.2653),
    "DOM": ("Dominica", -61.3000, 15.5470),
    "FDF": ("Martinique", -61.0032, 14.5910),
    "UVF": ("St. Lucia", -60.9526, 13.7332),
    "SVD": ("St. Vincent", -61.1550, 13.1500),
    "GND": ("Grenada", -61.7862, 12.0042),
    "BGI": ("Barbados", -59.4925, 13.0746),
    "POS": ("Trinidad", -61.3372, 10.5954),
    "TAB": ("Tobago", -60.8322, 11.1497),
    "AUA": ("Aruba", -70.0152, 12.5014),
    "CUR": ("Curacao", -68.9598, 12.1889),
    "BON": ("Bonaire", -68.2685, 12.1310),
}

# Island road networks each arrival airport serves (west, south, east, north).
HAWAII = (-160.6, 18.8, -154.7, 22.4)
PUERTO_RICO = (-67.4, 17.8, -65.2, 18.6)
BERMUDA = (-64.95, 32.2, -64.6, 32.45)
CARIBBEAN = {
    "cuba": ((-85.2, 19.6, -73.9, 23.4), ["HAV", "HOG"]),
    "jamaica": ((-78.5, 17.6, -76.1, 18.7), ["MBJ", "KIN"]),
    "hispaniola": ((-74.6, 17.4, -68.2, 20.2), ["SDQ", "PUJ", "STI", "PAP"]),
    "bahamas": ((-80.6, 20.8, -72.6, 27.4), ["NAS", "FPO", "MHH", "ELH", "GGT"]),
    "cayman": ((-81.5, 19.2, -79.6, 19.8), ["GCM"]),
    "turks_caicos": ((-72.6, 21.1, -70.9, 22.0), ["PLS"]),
    "virgin_islands": ((-65.2, 17.6, -64.2, 18.8), ["STT", "STX", "EIS"]),
    "lesser_antilles": ((-63.4, 11.9, -59.3, 18.4),
                        ["AXA", "SXM", "SKB", "ANU", "PTP", "DOM", "FDF", "UVF",
                         "SVD", "GND", "BGI"]),
    "trinidad_tobago": ((-62.0, 10.0, -60.4, 11.4), ["POS", "TAB"]),
    "abc_islands": ((-70.2, 11.9, -68.1, 12.8), ["AUA", "CUR", "BON"]),
}
# Hubs only connect: Miami is on the mainland road network, so it drives
# nothing (an empty box never overlaps a map region).
NOWHERE = (0.0, 0.0, 0.0, 0.0)
SERVES = {"HNL": HAWAII, "OGG": HAWAII, "KOA": HAWAII, "ITO": HAWAII,
          "LIH": HAWAII, "SJU": PUERTO_RICO, "MIA": NOWHERE, "BDA": BERMUDA}
SERVES.update({code: box for box, codes in CARIBBEAN.values() for code in codes})

# (from, to); a "from" other than the home airport is a connection.
# Every Caribbean island, Puerto Rico included, connects through Miami,
# the way most real trips from Boston to the small islands do. Bermuda is a
# direct flight (it's due southeast; a Miami detour would make no sense).
HUB = "MIA"
ROUTES = [("BOS", "HNL"), ("BOS", "BDA"), ("BOS", HUB), (HUB, "SJU"),
          ("HNL", "OGG"), ("HNL", "KOA"), ("HNL", "ITO"), ("HNL", "LIH")]
ROUTES += [(HUB, code) for _, codes in CARIBBEAN.values() for code in codes]


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
