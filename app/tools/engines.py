"""Engine picker: which OSRM containers are configured and which answer."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app import config, osrm
from app.registry import ToolMeta

meta = ToolMeta(id="engines", title="Engines", order=0, needs_engine=False, icon="⚙")
router = APIRouter()


@router.get("")
async def engines(request: Request) -> list[dict]:
    client = request.app.state.client
    healthy = {e.id for e in await osrm.healthy_engines(client)}
    return [{"id": e.id, "title": e.title, "url": e.url, "scope": e.scope, "color": e.color,
             "compose_service": e.compose_service, "healthy": e.id in healthy}
            for e in config.ENGINES.values()]
