"""Flight legs (amusement-parks rule, Max 2026-10-13): drive to an airport,
fly great-circle at FLIGHT_MPH plus AIRPORT_H, drive on. Mainland <-> island
uses that island's hubs (airports with nonstops); island <-> island flies
directly; Hawaii's islands fly between their own airports."""

from __future__ import annotations

import math

FLIGHT_MPH = 550
AIRPORT_H = 2.0
AIRPORTS = {"BOS": (-71.0096, 42.3656), "JFK": (-73.7781, 40.6413), "EWR": (-74.1745, 40.6895),
            "PHL": (-75.2424, 39.8744), "IAD": (-77.4565, 38.9531), "CLT": (-80.9431, 35.2140),
            "ATL": (-84.4277, 33.6407), "MCO": (-81.3089, 28.4312), "MIA": (-80.2870, 25.7959),
            "FLL": (-80.1527, 26.0742), "DTW": (-83.3534, 42.2162), "ORD": (-87.9073, 41.9742),
            "MSP": (-93.2218, 44.8848), "DFW": (-97.0403, 32.8998), "IAH": (-95.3368, 29.9902),
            "DEN": (-104.6737, 39.8561), "PHX": (-112.0116, 33.4352), "LAS": (-115.1398, 36.0840),
            "LAX": (-118.4085, 33.9416), "SFO": (-122.3790, 37.6213), "SEA": (-122.3088, 47.4502),
            "PDX": (-122.5951, 45.5898),
            "SJU": (-66.0018, 18.4394),
            "HNL": (-157.9251, 21.3245), "OGG": (-156.4305, 20.8986), "KOA": (-156.0456, 19.7388),
            "ITO": (-155.0485, 19.7203), "LIH": (-159.3390, 21.9760)}
ISLAND = {"PR": ["SJU"], "HI": ["HNL", "OGG", "KOA", "ITO", "LIH"]}
HUBS = {"PR": ["BOS", "JFK", "EWR", "PHL", "IAD", "CLT", "ATL", "MCO", "MIA", "FLL", "ORD", "DTW", "DFW", "IAH"],
        "HI": ["LAX", "SFO", "SEA", "PDX", "PHX", "LAS", "DEN", "DFW", "IAH", "ORD", "ATL", "MSP"]}


def km(a, b) -> float:
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def flight_s(a, b) -> float:
    return (km(a, b) / 1.609344 / FLIGHT_MPH + AIRPORT_H) * 3600


def options(ri: str, rj: str) -> list[tuple[str, str]]:
    dep = ISLAND[ri] if ri in ISLAND else HUBS[rj]
    arr = ISLAND[rj] if rj in ISLAND else HUBS[ri]
    return [(a, b) for a in dep for b in arr if a != b]


def best_hop(i, j, ri, rj, to, frm, codes) -> tuple[float, tuple[str, str] | None]:
    """to[i, k]: store i to airport codes[k]; frm[k, j]: airport to store j."""
    ix = {c: k for k, c in enumerate(codes)}
    best = (math.inf, None)
    for a, b in options(ri, rj):
        da, db = to[i, ix[a]], frm[ix[b], j]
        if not (math.isnan(da) or math.isnan(db)):
            c = float(da) + flight_s(AIRPORTS[a], AIRPORTS[b]) + float(db)
            if c < best[0]:
                best = (c, (a, b))
    return best
