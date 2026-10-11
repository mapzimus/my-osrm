from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.main import create_app  # noqa: E402
from app.tests import fake_osrm  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # every configured engine is "healthy" and answered by the fake
    app = create_app()
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(fake_osrm.handle))
    fake_osrm.calls.clear()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def places_db(tmp_path):
    from app.setup.places_build import create_schema, insert_rows
    path = tmp_path / "places.sqlite"
    con = sqlite3.connect(path)
    create_schema(con)
    insert_rows(con, [
        (1, "Plattsburgh", "New York", "US", "city", 19000, -73.4529, 44.6995),
        (2, "Platte City", "Missouri", "US", "town", 5000, -94.78, 39.37),
        (3, "Saint John", "New Brunswick", "CA", "city", 70000, -66.0633, 45.2733),
        (4, "Boston", "Massachusetts", "US", "city", 675000, -71.0589, 42.3601),
    ])
    con.commit()
    con.close()
    return path
