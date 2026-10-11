"""Country exclusion mask: synthetic shapefile, then through the isochrone job."""

from __future__ import annotations

import sys
import time

import numpy as np
import pytest

from app import config
from app.tests import fake_osrm

sys.path.insert(0, str(config.ISOCHRONE_DIR))
import countrymask  # noqa: E402

BOS = [-71.0589, 42.3601]


@pytest.fixture()
def fake_shp(tmp_path):
    """Two square 'countries': XA west of lon -72, XB east of it (lat 40-45)."""
    import shapefile
    path = tmp_path / "countries.shp"
    with shapefile.Writer(str(path)) as w:
        w.field("ISO_A2", "C", size=3)
        w.field("ISO_A2_EH", "C", size=3)
        w.field("ADM0_A3", "C", size=3)
        w.poly([[[-75, 40], [-72, 40], [-72, 45], [-75, 45], [-75, 40]]])
        w.record("XA", "XA", "XAA")
        w.poly([[[-72, 40], [-69, 40], [-69, 45], [-72, 45], [-72, 40]]])
        w.record("-99", "XB", "XBB")          # ISO_A2 missing, like France/Norway in NE
    return path


def test_mask_and_groups(fake_shp):
    lons = np.arange(-74.5, -69.5, 0.5)
    lats = np.arange(44.5, 40.0, -0.5)
    m = countrymask.country_mask(lons, lats, ["XA"], shp=fake_shp)
    assert m.shape == (len(lats), len(lons))
    assert m[:, lons < -72].all() and not m[:, lons > -72].any()
    m2 = countrymask.country_mask(lons, lats, ["xb"], shp=fake_shp)      # case-insensitive, ISO_A2_EH fallback
    assert m2[:, lons > -72].all() and not m2[:, lons < -72].any()
    assert countrymask.country_mask(lons, lats, [], shp=fake_shp).sum() == 0
    assert countrymask.expand(["CAM", "ca"]) == ["BZ", "CA", "CR", "GT", "HN", "NI", "PA", "SV"]


def wait_job(client, job_id, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}", params={"result": True}).json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_isochrone_job_with_exclusion(client, monkeypatch, tmp_path, fake_shp):
    from app.tools import isochrone as tool
    iso = tool.iso
    monkeypatch.setattr(iso, "CACHE", tmp_path)
    monkeypatch.setattr(countrymask, "SHP", fake_shp)
    monkeypatch.setitem(countrymask.GROUPS, "XA", ["XA"])
    requested: list[list] = []

    def fake_table(url, origin, pts):
        requested.extend(pts)
        return [fake_osrm.hav_m(origin, p) / 26.8 for p in pts], [100.0] * len(pts)
    monkeypatch.setattr(iso, "table", fake_table)

    body = {"name": "Excl", "origin": BOS, "bbox": [-74.5, 40.5, -69.5, 44.5], "step": 0.25, "hours": 3,
            "fill": False, "exclude": ["XA"]}
    res = wait_job(client, client.post("/api/isochrone/jobs", json=body).json()["id"])
    assert res["status"] == "done", res
    assert res["result"]["exclude"] == ["XA"]
    west = [p for p in requested if p[0] < -72 and abs(p[0] * 4 - round(p[0] * 4)) < 1e-6]   # grid cells only (city lookups are separate)
    assert requested and not west, west[:10]   # nothing west of -72 was asked for (boundary cells count as outside)
    assert any(f.name.startswith("exclude_") for f in tmp_path.iterdir())

    bad = client.post("/api/isochrone/jobs", json={**body, "exclude": ["ZZ"]})
    assert bad.status_code == 422
