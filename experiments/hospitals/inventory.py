"""Every hospital with an emergency room open to everyone, lower 48 (idea #212).

Source: CMS "Hospital General Information" (Medicare-certified hospitals), the
CSV in cache/. Kept (Max, 2026-10-14: "real hospitals, not walk-in clinics"):
Acute Care, Critical Access and Rural Emergency hospitals with Emergency
Services = Yes. Out: psychiatric, children's-only, VA and Department of
Defense hospitals (not open to everyone), anything without an ER.
Addresses geocoded with the US Census batch geocoder (cache/geocode_batch.csv);
the ones it can't place go to Nominatim one by one (cache/geocode_fallback.json),
and the rest to their ZIP code's centre (Census 2024 ZCTA gazetteer): a few km
off at worst, where leaving a rural hospital out would draw a fake medical desert.

    python experiments/hospitals/inventory.py

Writes cache/stores.csv (chain, name, lon, lat, state) for the Turf Wars code
(experiments/dunkin-vs-starbucks/drivetime.py); one "chain": hospital.
"""

from __future__ import annotations

import csv
import io
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
CMS_URL = ("https://data.cms.gov/provider-data/sites/default/files/resources/"
           "893c372430d9d71a1c52737d01239d47_1785189955/Hospital_General_Information.csv")
TYPES = {"Acute Care Hospitals", "Critical Access Hospitals", "Rural Emergency Hospital"}
NOT_L48 = {"AK", "HI", "PR", "GU", "VI", "AS", "MP"}


def fetch() -> list[dict]:
    path = CACHE / "Hospital_General_Information.csv"
    if not path.exists():
        urllib.request.urlretrieve(CMS_URL, path)
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))


def batch_geocode(rows: list[dict]) -> dict[str, tuple[float, float]]:
    """Census batch geocoder: one CSV upload (max 10,000 rows), cached."""
    out_path = CACHE / "geocode_batch.csv"
    if not out_path.exists():
        body = io.StringIO()
        w = csv.writer(body)
        for r in rows:
            w.writerow([r["Facility ID"], r["Address"], r["City/Town"], r["State"], r["ZIP Code"]])
        boundary = "----hospitals"
        data = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"benchmark\"\r\n\r\nPublic_AR_Current\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"addressFile\"; filename=\"h.csv\"\r\n"
                f"Content-Type: text/csv\r\n\r\n{body.getvalue()}\r\n--{boundary}--\r\n").encode()
        req = urllib.request.Request("https://geocoding.geo.census.gov/geocoder/locations/addressbatch", data=data,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=900) as r:
            out_path.write_bytes(r.read())
    found = {}
    for row in csv.reader(open(out_path, encoding="utf-8", errors="replace")):
        if len(row) >= 6 and row[2] == "Match" and row[5]:
            lon, lat = map(float, row[5].split(","))
            found[row[0]] = (lon, lat)
    return found


def nominatim(r: dict, cache: dict) -> tuple[float, float] | None:
    k = r["Facility ID"]
    if k not in cache:
        q = urllib.parse.urlencode(dict(street=r["Address"], city=r["City/Town"], state=r["State"],
                                        postalcode=r["ZIP Code"], country="USA", format="json", limit=1))
        req = urllib.request.Request(f"https://nominatim.openstreetmap.org/search?{q}",
                                     headers={"User-Agent": "my-osrm hospitals video (mapzimus)"})
        time.sleep(1.1)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                m = json.load(resp)
        except OSError:
            return None
        cache[k] = [float(m[0]["lon"]), float(m[0]["lat"])] if m else None
        json.dump(cache, open(CACHE / "geocode_fallback.json", "w"), indent=0)
    return tuple(cache[k]) if cache[k] else None


def zip_centres() -> dict[str, tuple[float, float]]:
    path = CACHE / "2024_Gaz_zcta_national.txt"
    if not path.exists():
        import zipfile
        zp = CACHE / "zcta.zip"
        urllib.request.urlretrieve("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/"
                                   "2024_Gaz_zcta_national.zip", zp)
        zipfile.ZipFile(zp).extract(path.name, CACHE)
    out = {}
    for row in csv.DictReader(open(path, encoding="utf-8"), delimiter="	"):
        row = {k.strip(): v.strip() for k, v in row.items()}
        out[row["GEOID"]] = (float(row["INTPTLONG"]), float(row["INTPTLAT"]))
    return out


def main() -> None:
    CACHE.mkdir(exist_ok=True)
    rows = [r for r in fetch() if r["Hospital Type"] in TYPES and r["Emergency Services"] == "Yes"
            and r["State"] not in NOT_L48]
    print(f"{len(rows):,} hospitals with an ER open to everyone in the lower 48", flush=True)
    found = batch_geocode(rows)
    print(f"Census batch placed {len(found):,}", flush=True)
    fpath = CACHE / "geocode_fallback.json"
    fb = json.load(open(fpath)) if fpath.exists() else {}
    zips = zip_centres()
    out, missing, how = [], [], {"address": 0, "nominatim": 0, "zip centre": 0}
    for r in rows:
        ll, src = found.get(r["Facility ID"]), "address"
        if ll is None:
            ll, src = nominatim(r, fb), "nominatim"
        if ll is None:
            ll, src = zips.get(r["ZIP Code"][:5].zfill(5)), "zip centre"
        if ll is None:
            missing.append(f"{r['Facility Name']}, {r['City/Town']} {r['State']}")
            continue
        how[src] += 1
        out.append(dict(chain="hospital", name=r["Facility Name"].title(), lon=round(ll[0], 6), lat=round(ll[1], 6),
                        state=r["State"], type=r["Hospital Type"]))
    with open(CACHE / "stores.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["chain", "name", "lon", "lat", "state", "type"])
        w.writeheader()
        w.writerows(out)
    print(f"{len(out):,} placed {how}; {len(missing)} not found: {missing[:10]}")


if __name__ == "__main__":
    main()
