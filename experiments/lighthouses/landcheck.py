"""Is a point on land or in water? Reads the local Protomaps basemap
(data/basemap/north-america.pmtiles): a point counts as water when it falls
inside any `water` polygon (ocean, lake, river)."""

from __future__ import annotations

import gzip
import math
from functools import lru_cache
from pathlib import Path

import mapbox_vector_tile
from pmtiles.reader import MmapSource, Reader
from shapely.geometry import Point, shape

PMTILES = Path(__file__).resolve().parents[2] / "data" / "basemap" / "north-america.pmtiles"
_f = open(PMTILES, "rb")
_r = Reader(MmapSource(_f))
Z = min(_r.header()["max_zoom"], 15)


def _tile(lon: float, lat: float, z: int = Z) -> tuple[int, int, float, float]:
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return int(x), int(y), x % 1, y % 1


@lru_cache(maxsize=4096)
def _layers(z: int, x: int, y: int) -> dict:
    data = _r.get(z, x, y)
    if not data:
        return {}
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return mapbox_vector_tile.decode(data, default_options={"y_coord_down": True})


def surface(lon: float, lat: float) -> str:
    """'land', 'water', or 'unknown' (no tile)."""
    x, y, fx, fy = _tile(lon, lat)
    L = _layers(Z, x, y)
    if not L:
        return "unknown"
    def hit(name: str) -> bool:
        lay = L.get(name)
        if not lay:
            return False
        p = Point(fx * lay["extent"], fy * lay["extent"])
        return any(f["geometry"]["type"] in ("Polygon", "MultiPolygon")
                   and shape(f["geometry"]).buffer(0).contains(p) for f in lay["features"])
    # Lakes (the Great Lakes too) are water polygons on top of earth, so
    # water wins over earth.
    return "water" if hit("water") else "land"
