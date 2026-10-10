"""Rasterize Natural Earth land minus lakes onto an isochrone grid.

Runs in the my-osrm-tools container (shapely + pyshp):

    python landmask.py <grid.npz> <out_mask.npz> <ne_10m_land.shp> <ne_10m_lakes.shp>

The grid .npz is the cache written by isochrone.py (holds lons, lats).
Output: mask[row, col] = True where the cell center is on land.
"""

import sys

import numpy as np
import shapefile
import shapely
from shapely.geometry import box, shape
from shapely.ops import unary_union


def load(path: str, bbox):
    with shapefile.Reader(path) as shp:
        geoms = [shape(s.__geo_interface__) for s in shp.shapes()]
    clip = box(*bbox)
    return unary_union([g.intersection(clip) for g in geoms if g.intersects(clip)])


def main(grid_npz, out_npz, land_shp, lakes_shp):
    g = np.load(grid_npz)
    lons, lats = g["lons"], g["lats"]
    bbox = (lons.min() - 1, lats.min() - 1, lons.max() + 1, lats.max() + 1)
    land = load(land_shp, bbox).difference(load(lakes_shp, bbox))
    shapely.prepare(land)
    lon_g, lat_g = np.meshgrid(lons, lats)
    mask = shapely.contains_xy(land, lon_g.ravel(), lat_g.ravel()).reshape(lon_g.shape)
    np.savez_compressed(out_npz, mask=mask)
    print(f"land cells: {mask.sum():,} of {mask.size:,}")


if __name__ == "__main__":
    main(*sys.argv[1:])
