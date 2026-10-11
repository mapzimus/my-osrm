"""FastAPI app factory. Routes:

    /                      app/web (index.html + JS/CSS)
    /tools/<id>.js         app/web/tools
    /vendor/...            vendored libraries, fonts, sprites (app/setup)
    /basemap/<file>        data/basemap/*.pmtiles (HTTP Range via Starlette FileResponse)
    /api/tools             tool manifest
    /api/jobs/...          background jobs
    /api/<tool>/...        one router per app/tools/<tool>.py
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import config, jobs, registry


def create_app() -> FastAPI:
    tools = registry.discover()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "client", None) is None:
            app.state.client = httpx.AsyncClient()
        yield
        await app.state.client.aclose()

    app = FastAPI(title="my-osrm", lifespan=lifespan)
    app.state.client = None            # tests set a MockTransport client before startup
    app.state.jobs = jobs.manager
    app.state.tools = [m for m, _ in tools]

    @app.get("/api/tools")
    def list_tools() -> list[dict]:
        return [m.public() for m in app.state.tools]

    @app.get("/api/config")
    def app_config() -> dict:
        files = sorted(p.name for p in config.BASEMAP_DIR.glob("*.pmtiles")) if config.BASEMAP_DIR.exists() else []
        return {
            "engines": [{"id": e.id, "title": e.title, "url": e.url, "scope": e.scope, "color": e.color}
                        for e in config.ENGINES.values()],
            "default_engine": config.DEFAULT_ENGINE,
            "basemaps": files,
            "places_index": (config.APP_DATA / "places.sqlite").exists(),
            "ferries_overlay": (config.APP_DATA / "ferries.geojson").exists(),
        }

    app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
    for meta, router in tools:
        app.include_router(router, prefix=f"/api/{meta.id}", tags=[meta.id])

    config.BASEMAP_DIR.mkdir(parents=True, exist_ok=True)
    config.VENDOR.mkdir(parents=True, exist_ok=True)
    app.mount("/basemap", StaticFiles(directory=config.BASEMAP_DIR), name="basemap")
    app.mount("/vendor", StaticFiles(directory=config.VENDOR), name="vendor")
    app.mount("/tools", StaticFiles(directory=config.WEB / "tools"), name="tools")
    app.mount("/", StaticFiles(directory=config.WEB, html=True), name="web")
    return app
