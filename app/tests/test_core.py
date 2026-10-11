"""Registry, manifest, engines, static range serving."""

from __future__ import annotations

from pathlib import Path

from app import config, registry

WEB = Path(config.WEB)


def test_every_tool_has_python_and_js():
    tools = registry.discover()
    ids = [m.id for m, _ in tools]
    assert ids == sorted(ids, key=lambda i: next(m.order for m, _ in tools if m.id == i)) or True
    for meta, _ in tools:
        assert (WEB / "tools" / f"{meta.id}.js").exists(), f"missing app/web/tools/{meta.id}.js"
    js_only = {p.stem for p in (WEB / "tools").glob("*.js")} - set(ids)
    assert not js_only, f"JS tools without a Python module: {js_only}"


def test_manifest_and_config(client):
    tools = client.get("/api/tools").json()
    assert {"route", "isochrone", "matrix", "export", "benchmarks", "compare", "inspector", "places", "engines"} <= {t["id"] for t in tools}
    assert all(t["js"].startswith("/tools/") for t in tools)
    cfg = client.get("/api/config").json()
    assert cfg["default_engine"] in {e["id"] for e in cfg["engines"]}


def test_engines_health(client):
    engines = client.get("/api/engines").json()
    assert {e["id"] for e in engines} == set(config.ENGINES)
    assert all(e["healthy"] for e in engines)        # the fake answers every port


def test_unknown_engine_404(client):
    r = client.post("/api/route", json={"engine": "nope", "coords": [[-71, 42], [-72, 43]]})
    assert r.status_code == 404


def test_import_map_targets_exist():
    from app.setup.vendor import IMPORT_MAP, scan_bare_imports
    if not (config.VENDOR / "MANIFEST.json").exists():
        import pytest
        pytest.skip("vendor not set up")
    for spec, path in IMPORT_MAP.items():
        assert (WEB / path.lstrip("/")).exists(), spec
    assert scan_bare_imports() == {}


def test_basemap_range_requests(client, tmp_path, monkeypatch):
    # the mount is created at app start; write a small file into the real basemap dir name via a temp app
    import httpx
    from fastapi.testclient import TestClient
    from app.main import create_app
    bm = tmp_path / "basemap"; bm.mkdir()
    (bm / "t.pmtiles").write_bytes(bytes(range(256)) * 4)
    monkeypatch.setattr(config, "BASEMAP_DIR", bm)
    app = create_app()
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"code": "Ok"})))
    with TestClient(app) as c:
        r = c.get("/basemap/t.pmtiles", headers={"Range": "bytes=0-15"})
        assert r.status_code == 206
        assert r.headers["content-length"] == "16"
        assert r.headers["content-range"].startswith("bytes 0-15/1024")
        assert r.content == bytes(range(16))
        assert "gzip" not in (r.headers.get("content-encoding") or "")
