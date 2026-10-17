"""The ferry overlay knows which engine network it was built from (app/setup/ferries.py)."""

from __future__ import annotations

import json

from app.setup import ferries


def _point(monkeypatch, tmp_path):
    monkeypatch.setattr(ferries, "NETWORK", tmp_path / "na-ferry.osm.pbf")
    monkeypatch.setattr(ferries, "OUT", tmp_path / "ferries.geojson")
    monkeypatch.setattr(ferries, "SRC", tmp_path / "ferries.src.json")


def test_no_overlay_or_no_record_is_not_stale(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    assert ferries.fingerprint() is None and not ferries.stale()
    ferries.OUT.write_text("{}")
    assert not ferries.stale(), "an overlay from before the record existed is trusted"


def test_rebuilt_network_makes_the_overlay_stale(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    ferries.NETWORK.write_bytes(b"x" * 10)
    ferries.OUT.write_text("{}")
    ferries.SRC.write_text(json.dumps(ferries.fingerprint()))
    assert not ferries.stale()
    ferries.NETWORK.write_bytes(b"y" * 11)                  # a new build: different size
    assert ferries.stale()
    ferries.SRC.write_text("not json")
    assert not ferries.stale(), "a broken record never blocks the app"


def test_run_records_the_network_for_an_old_overlay(monkeypatch, tmp_path):
    _point(monkeypatch, tmp_path)
    ferries.NETWORK.write_bytes(b"x" * 10)
    ferries.OUT.write_text("{}")
    assert ferries.run(tmp_path) == ferries.OUT                # skips, no docker
    assert json.loads(ferries.SRC.read_text()) == ferries.fingerprint()
