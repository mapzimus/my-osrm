"""Which grid cells fall inside given countries (Natural Earth admin-0).

    mask = country_mask(lons, lats, ["CA", "MX"])     # True = inside one of them

Runs on the host (shapely + pyshp). The shapefile comes from
`python -m app.setup --only countries` -> data/naturalearth/ne_10m_admin_0_countries/.
Groups: CAM = Central America (Guatemala .. Panama).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
NE_DIR = HERE.parent.parent / "data" / "naturalearth" / "ne_10m_admin_0_countries"
SHP = NE_DIR / "ne_10m_admin_0_countries.shp"

GROUPS = {
    "US": ["US"], "CA": ["CA"], "MX": ["MX"],
    "CAM": ["GT", "BZ", "SV", "HN", "NI", "CR", "PA"],
}
LABELS = {"US": "United States", "CA": "Canada", "MX": "Mexico", "CAM": "Central America"}


def expand(codes) -> list[str]:
    out: list[str] = []
    for c in codes:
        c = c.strip().upper()
        out += GROUPS.get(c, [c])
    return sorted(set(out))


def iso_a2(rec: dict) -> str:
    # ISO_A2 is "-99" for a few countries; ISO_A2_EH / ADM0_A3 fill in
    for k in ("ISO_A2_EH", "ISO_A2"):
        v = rec.get(k)
        if v and v != "-99":
            return v
    return rec.get("ADM0_A3", "")[:2]


def load_union(codes: list[str], bbox, shp: Path | None = None):
    shp = shp or SHP
    import shapefile
    from shapely.geometry import box, shape
    from shapely.ops import unary_union

    if not shp.exists():
        raise FileNotFoundError(f"{shp} missing: run  python -m app.setup --only countries")
    wanted = set(codes)
    clip = box(*bbox)
    geoms = []
    with shapefile.Reader(str(shp)) as r:
        for sr in r.iterShapeRecords():
            if iso_a2(sr.record.as_dict()) not in wanted:
                continue
            g = shape(sr.shape.__geo_interface__)
            if g.intersects(clip):
                geoms.append(g.intersection(clip))
    return unary_union(geoms) if geoms else None


def country_mask(lons: np.ndarray, lats: np.ndarray, codes, shp: Path | None = None) -> np.ndarray:
    """Boolean [row, col] grid: cell center inside any of `codes` (groups allowed)."""
    import shapely

    lon_g, lat_g = np.meshgrid(lons, lats)
    mask = np.zeros(lon_g.shape, dtype=bool)
    codes = expand(codes)
    if not codes:
        return mask
    bbox = (float(lons.min()) - 0.5, float(lats.min()) - 0.5, float(lons.max()) + 0.5, float(lats.max()) + 0.5)
    geom = load_union(codes, bbox, shp or SHP)
    if geom is None or geom.is_empty:
        return mask
    shapely.prepare(geom)
    return shapely.contains_xy(geom, lon_g.ravel(), lat_g.ravel()).reshape(lon_g.shape)
