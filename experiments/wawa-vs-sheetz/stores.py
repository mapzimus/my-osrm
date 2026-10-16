"""Turf Wars: Wawa vs. Sheetz, the Mid-Atlantic frontline (Max, 2026-10-16: regional, since New
England has neither; then "include every Wawa and Sheetz, but restrict the expanding drives to the
states that make sense"). Every store of both chains; roads and people count only in the states
where either chain has a store (REGION, from the data), so the drives never flood New England.

Stores: AllThePlaces wawa, sheetz (run in cache/atp/run.txt): 943 and 865, but ATP has no Wawa in
Delaware at all (Wawa says 1,100+ stores). So OpenStreetMap is merged in (osmium tags-filter
brand:wikidata Q5936320 / Q7492551 or name on us-latest.osm.pbf -> data/scratch/wawa_sheetz.geojson):
an OSM store (its shop and fuel features within OSM_MERGE_KM are one) counts only when no ATP store of
the same chain is within OSM_MERGE_KM. Points closer than DEDUPE_M (same chain) are one store.
Region gaps: FILL adds the in-between states with no store (Max: "this will look weird with gaps").
Roads: dunkin-vs-starbucks/cache/roads.npz (lower-48 major roads); cache/road_mask.npy keeps the
points inside REGION (Natural Earth states, + STATE_SLOP_DEG) for drivetime.py --mask.

    python experiments/wawa-vs-sheetz/stores.py

Writes cache/stores.csv (chain, name, lon, lat, state) and cache/road_mask.npy.
"""

from __future__ import annotations

import collections
import csv
import json
import math
from pathlib import Path

import numpy as np
import shapefile
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.prepared import prep

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = HERE / "cache"
CHAINS = {"wawa": "wawa", "sheetz": "sheetz"}       # chain -> AllThePlaces spider (wawa first: colour 0)
ABBR = {"PA": "Pennsylvania", "NJ": "New Jersey", "DE": "Delaware", "MD": "Maryland", "DC": "District of Columbia",
        "VA": "Virginia", "WV": "West Virginia", "OH": "Ohio", "NC": "North Carolina", "FL": "Florida",
        "IN": "Indiana", "MI": "Michigan", "KY": "Kentucky", "NY": "New York", "TN": "Tennessee",
        "SC": "South Carolina", "GA": "Georgia", "AL": "Alabama", "IL": "Illinois"}
DEDUPE_M = 30
OSM_MERGE_KM = 0.3
OSM = ROOT / "data" / "scratch" / "wawa_sheetz.geojson"
WIKIDATA = {"Q5936320": "wawa", "Q7492551": "sheetz"}
FILL = {"South Carolina", "Delaware", "Georgia"}          # in-between states (no store, or ATP missed them)
STATE_SLOP_DEG = 0.03


def main() -> None:
    rows = []
    for chain, spider in CHAINS.items():
        seen = []
        for f in json.load(open(CACHE / "atp" / f"{spider}.geojson", encoding="utf-8"))["features"]:
            if not f.get("geometry"):
                continue
            lon, lat = f["geometry"]["coordinates"]
            if any(math.hypot((lon - a) * 111e3 * math.cos(math.radians(lat)), (lat - b) * 111e3) < DEDUPE_M for a, b in seen):
                continue
            seen.append((lon, lat))
            p = f["properties"]
            rows.append(dict(chain=chain, name=p.get("name") or chain.title(), lon=lon, lat=lat,
                             state=p.get("addr:state", ""), src="atp"))
    # OpenStreetMap: stores ATP missed
    from scipy.cluster.hierarchy import fcluster, linkage
    added = collections.Counter()
    for chain in CHAINS:
        pts = []
        for f in json.load(open(OSM, encoding="utf-8"))["features"]:
            q = f["properties"]
            c = WIKIDATA.get(q.get("brand:wikidata", "")) or (q.get("name", "").lower() if q.get("name", "").lower() in CHAINS else None)
            if c != chain or not (q.get("shop") or q.get("amenity") in ("fuel",)):
                continue
            g = shape(f["geometry"]).representative_point()
            pts.append((g.x, g.y, q.get("addr:state", "")))
        if len(pts) < 2:
            continue
        xy = np.array([(x * 111.32 * math.cos(math.radians(y)), y * 110.57) for x, y, _ in pts])
        groups = collections.defaultdict(list)
        for k, q in zip(fcluster(linkage(xy, "single"), OSM_MERGE_KM, "distance"), pts):
            groups[k].append(q)
        mine = np.array([(r_["lon"], r_["lat"]) for r_ in rows if r_["chain"] == chain])
        for v in groups.values():
            lon, lat = float(np.mean([q[0] for q in v])), float(np.mean([q[1] for q in v]))
            d = np.hypot((mine[:, 0] - lon) * 111.32 * math.cos(math.radians(lat)), (mine[:, 1] - lat) * 110.57).min()
            if d > OSM_MERGE_KM:
                st = next((q[2] for q in v if q[2]), "")
                rows.append(dict(chain=chain, name=chain.title(), lon=lon, lat=lat, state=st, src="osm"))
                added[chain] += 1
    print("added from OSM:", dict(added))
    # OSM stores without addr:state: the state polygon they sit in
    sf0 = shapefile.Reader(str(ROOT / "data" / "naturalearth" / "ne_10m_admin_1_states_provinces"
                               / "ne_10m_admin_1_states_provinces.shp"))
    polys = [(shape(sh.__geo_interface__), rec["postal"]) for sh, rec in zip(sf0.shapes(), sf0.records()) if rec["adm0_a3"] == "USA"]
    from shapely.geometry import Point as _P
    for r_ in rows:
        if not r_["state"]:
            pt = _P(r_["lon"], r_["lat"])
            r_["state"] = next((code for g, code in polys if g.contains(pt)), min(polys, key=lambda gc: gc[0].distance(pt))[1])
    with open(CACHE / "stores.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["chain", "name", "lon", "lat", "state", "src"])
        w.writeheader()
        w.writerows(rows)
    REGION = {ABBR[r_["state"]] for r_ in rows if r_["state"] in ABBR} | FILL   # states with a store + gaps
    print("states with a store:", sorted(REGION))
    json.dump(sorted(REGION), open(CACHE / "region.json", "w"))
    sf = shapefile.Reader(str(ROOT / "data" / "naturalearth" / "ne_10m_admin_1_states_provinces"
                              / "ne_10m_admin_1_states_provinces.shp"))
    region = unary_union([shape(s.__geo_interface__) for s, r in zip(sf.shapes(), sf.records())
                          if r["adm0_a3"] == "USA" and r["name"] in REGION]).buffer(STATE_SLOP_DEG)
    r = np.load(HERE.parent / "dunkin-vs-starbucks" / "cache" / "roads.npz")
    lon, lat = r["lon"], r["lat"]
    x0, y0, x1, y1 = region.bounds
    box = (lon > x0) & (lon < x1) & (lat > y0) & (lat < y1)
    pr = prep(region)
    from shapely.geometry import Point
    mask = np.zeros(len(lon), bool)
    idx = np.flatnonzero(box)
    mask[idx] = [pr.contains(Point(a, b)) for a, b in zip(lon[idx], lat[idx])]
    np.save(CACHE / "road_mask.npy", mask)
    by = collections.Counter((r_["chain"], r_["state"]) for r_ in rows)
    print(f"{len(rows)} stores: {collections.Counter(r_['chain'] for r_ in rows)}; states not mapped: "
          f"{sorted({r_['state'] for r_ in rows if r_['state'] not in ABBR})}")
    print(f"road points in the region: {mask.sum():,} of {len(mask):,}")
    print(sorted(by.items(), key=lambda kv: -kv[1])[:14])


if __name__ == "__main__":
    main()
