"""Same route on every engine that's up: what Canada, Mexico and ferries buy you."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app import config, osrm
from app.registry import ToolMeta

meta = ToolMeta(id="compare", title="Compare", order=50, icon="≡")
router = APIRouter()


class CompareIn(BaseModel):
    coords: list[list[float]] = Field(min_length=2)
    engines: list[str] | None = None        # default: every healthy engine


@router.post("")
async def compare(body: CompareIn, request: Request) -> dict:
    client = request.app.state.client
    wanted = [config.ENGINES[e] for e in (body.engines or config.ENGINES) if e in config.ENGINES]
    healthy = await osrm.healthy_engines(client)
    engines = [e for e in wanted if e in healthy]

    async def one(eng: config.Engine) -> dict:
        try:
            data = await osrm.route(client, eng, body.coords, steps=True)
        except osrm.NoRoute:
            return {"engine": eng.id, "title": eng.title, "color": eng.color, "status": "no route"}
        except Exception as exc:  # noqa: BLE001 - one engine failing shouldn't sink the table
            return {"engine": eng.id, "title": eng.title, "color": eng.color, "status": f"error: {exc}"}
        s = osrm.summarize(data)
        return {"engine": eng.id, "title": eng.title, "color": eng.color, "status": "ok",
                "miles": s["miles"], "hours": s["hours"], "used_ferry": s["used_ferry"],
                "geometry": s["geometry"], "snap_m": max((w["snap_m"] for w in s["waypoints"]), default=0)}

    results = await asyncio.gather(*(one(e) for e in engines))
    skipped = [e.id for e in wanted if e not in healthy]
    return {"results": list(results), "skipped": skipped}
