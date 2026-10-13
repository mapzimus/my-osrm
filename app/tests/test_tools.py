"""One test group per tool, against the fake OSRM."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from app import config
from app.tests import fake_osrm

BOS = [-71.0589, 42.3601]
PLB = [-73.4529, 44.6995]
SEA = [-122.3340, 47.6050]
BAIN = [-122.5210, 47.6262]


# ---- route -------------------------------------------------------------------
def test_route_basic(client):
    r = client.post("/api/route", json={"coords": [BOS, PLB]}).json()
    assert r["engine"] == config.DEFAULT_ENGINE
    assert r["miles"] > 100 and r["hours"] > 1
    assert r["geometry"]["type"] == "LineString"
    assert [s["type"] for s in r["steps"]][0] == "depart"
    assert r["used_ferry"] is False
    assert len(r["waypoints"]) == 2


def test_route_ferry_flag_and_stops(client):
    r = client.post("/api/route", json={"coords": [SEA, BAIN, SEA]}).json()
    assert r["used_ferry"] is True
    assert len(r["legs"]) == 2


def test_route_noroute(client):
    r = client.post("/api/route", json={"coords": [BOS, [0, 0]]})
    assert r.status_code == 404
    assert "no route" in r.json()["detail"]


def test_trip_order(client):
    r = client.post("/api/route/trip", json={"coords": [BOS, PLB, SEA], "roundtrip": False}).json()
    assert sorted(r["order"]) == [0, 1, 2]
    assert r["order"] == [2, 1, 0]          # fake reverses


# ---- matrix ------------------------------------------------------------------
def test_matrix_shape_and_csv(client):
    body = {"coords": [BOS, PLB, SEA], "names": ["Boston", "Plattsburgh", "Seattle"]}
    m = client.post("/api/matrix", json=body).json()
    assert len(m["hours"]) == 3 and all(len(row) == 3 for row in m["hours"])
    assert m["hours"][0][0] == 0
    assert m["miles"][0][2] > 2000
    csv = client.post("/api/matrix/csv?what=hours", json=body).text.splitlines()
    assert csv[0].startswith("hours (") and csv[0].endswith("Boston,Plattsburgh,Seattle")
    assert csv[1].startswith("Boston,0.00,")


def test_matrix_names_mismatch(client):
    r = client.post("/api/matrix", json={"coords": [BOS, PLB], "names": ["x"]})
    assert r.status_code == 422


# ---- export ------------------------------------------------------------------
def test_gpx(client):
    r = client.post("/api/export/gpx", json={"name": "Test route", "geometry": {"type": "LineString", "coordinates": [BOS, PLB]},
                                            "waypoints": [{"location": BOS, "name": "Boston"}, {"location": PLB}]})
    assert r.status_code == 200 and "gpx" in r.headers["content-type"]
    root = ET.fromstring(r.content)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    assert len(root.findall("g:trk/g:trkseg/g:trkpt", ns)) == 2
    assert [w.find("g:name", ns).text for w in root.findall("g:wpt", ns)] == ["Boston", "Stop 2"]


def test_gpx_rejects_polygon(client):
    r = client.post("/api/export/gpx", json={"geometry": {"type": "Point", "coordinates": BOS}})
    assert r.status_code == 422


# ---- compare -----------------------------------------------------------------
def test_compare_all_engines(client):
    r = client.post("/api/compare", json={"coords": [SEA, BAIN]}).json()
    assert {x["engine"] for x in r["results"]} == set(config.ENGINES)
    assert all(x["status"] == "ok" and x["used_ferry"] for x in r["results"])
    assert r["skipped"] == []


def test_compare_noroute_is_per_engine(client):
    r = client.post("/api/compare", json={"coords": [BOS, [0, 0]], "engines": ["na-ferry"]}).json()
    assert r["results"][0]["status"] == "no route"


# ---- inspector ---------------------------------------------------------------
def test_inspector_nearest(client):
    r = client.get("/api/inspector/nearest", params={"lon": BOS[0], "lat": BOS[1], "number": 2}).json()
    assert len(r["waypoints"]) == 2
    assert r["waypoints"][0]["nodes"] == [1000, 2000]
    assert r["probe"]["mode"] == "driving" and r["probe"]["mph"] > 0


def test_inspector_ferries_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DATA", tmp_path)
    assert client.get("/api/inspector/ferries.geojson").status_code == 404


# ---- benchmarks --------------------------------------------------------------
def wait_job(client, job_id, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}", params={"result": True}).json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_benchmarks_run(client, tmp_path, monkeypatch):
    from app.tools import benchmarks
    toml = tmp_path / "routes.toml"
    toml.write_text(f"""
[[case]]
name = "pass case"
kind = "control"
needs = "us"
from = {BOS}
to = {PLB}
max_miles = 400

[[case]]
name = "fail case (too far)"
kind = "control"
needs = "us"
from = {BOS}
to = {SEA}
max_miles = 10

[[case]]
name = "na only"
kind = "border"
needs = "na"
from = {BOS}
to = {PLB}
max_miles = 400
""")
    monkeypatch.setattr(benchmarks.router, "routes_file", toml, raising=False)
    monkeypatch.setattr(benchmarks.report_card, "route", lambda url, a, b, exclude="": (150.0, 3.0, False, 10.0) if b == PLB else (3000.0, 50.0, False, 10.0))
    assert len(client.get("/api/benchmarks/cases").json()) == 3
    job = client.post("/api/benchmarks/run", json={"engine": "us-major"}).json()
    res = wait_job(client, job["id"])
    assert res["status"] == "done", res
    rows = {r["name"]: r["result"] for r in res["result"]["rows"]}
    assert rows == {"pass case": "PASS", "fail case (too far)": "FAIL", "na only": "SKIP"}
    assert (res["result"]["passed"], res["result"]["scored"]) == (1, 2)


# ---- jobs --------------------------------------------------------------------
def test_job_cancel(client):
    from app import jobs

    def slow(job):
        for _ in range(100):
            job.update(0.1, "working")
            time.sleep(0.02)
        return "done"

    job = jobs.manager.submit("test", slow)
    client.delete(f"/api/jobs/{job.id}")
    res = wait_job(client, job.id)
    assert res["status"] == "cancelled"


def test_job_404(client):
    assert client.get("/api/jobs/nope").status_code == 404


# ---- places ------------------------------------------------------------------
def test_places_search(client, places_db, monkeypatch):
    from app.tools import places
    monkeypatch.setattr(places.router, "db_path", places_db, raising=False)
    r = client.get("/api/places", params={"q": "platt"}).json()
    assert [p["name"] for p in r] == ["Plattsburgh", "Platte City"]      # population order
    assert r[0]["label"] == "Plattsburgh, New York, US"
    assert client.get("/api/places", params={"q": "saint jo"}).json()[0]["name"] == "Saint John"
    assert client.get("/api/places", params={"q": "zzz"}).json() == []


def test_places_missing_index(client, tmp_path, monkeypatch):
    from app.tools import places
    monkeypatch.setattr(places.router, "db_path", tmp_path / "none.sqlite", raising=False)
    assert client.get("/api/places", params={"q": "bos"}).status_code == 503


# ---- isochrone ---------------------------------------------------------------
def test_isochrone_job(client, monkeypatch, tmp_path):
    from app.tools import isochrone as tool
    iso = tool.iso
    monkeypatch.setattr(iso, "CACHE", tmp_path)

    def fake_table(url, origin, pts):
        durs = [fake_osrm.hav_m(origin, p) / 26.8 for p in pts]
        return durs, [100.0] * len(pts)
    monkeypatch.setattr(iso, "table", fake_table)

    body = {"name": "Test", "origin": BOS, "bbox": [-72.5, 41.5, -70, 43.5], "step": 0.1, "hours": 3, "fill": False}
    job = client.post("/api/isochrone/jobs", json=body).json()
    res = wait_job(client, job["id"])
    assert res["status"] == "done", res
    out = res["result"]
    assert out["hours"] == 3 and len(out["colors"]) == 3
    assert out["bounds"][0][0] < BOS[1] < out["bounds"][1][0]
    png = client.get(out["png_url"])
    assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (tmp_path / "test_custom_-72.50_41.50_-70.00_43.50_0.1.npz").exists()


def test_isochrone_validation(client):
    assert client.post("/api/isochrone/jobs", json={"origin": BOS}).status_code == 422
    assert client.post("/api/isochrone/jobs", json={"origin": BOS, "region": "mars"}).status_code == 422
    r = client.post("/api/isochrone/jobs", json={"origin": BOS, "bbox": [-130, 20, -60, 60], "step": 0.01})
    assert r.status_code == 422 and "too fine" in r.json()["detail"]
