"""A -> B (-> C ...) driving route with turn list. Also /trip for best stop order."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import osrm
from app.registry import ToolMeta

meta = ToolMeta(id="route", title="Route", order=10, icon="A→B")
router = APIRouter()


class RouteIn(BaseModel):
    engine: str | None = None
    coords: list[list[float]] = Field(min_length=2, description="[[lon, lat], ...] in visit order")
    steps: bool = True


class TripIn(BaseModel):
    engine: str | None = None
    coords: list[list[float]] = Field(min_length=3)
    roundtrip: bool = True


@router.post("")
async def route(body: RouteIn, request: Request) -> dict:
    eng = osrm.engine_or_404(body.engine)
    try:
        data = await osrm.route(request.app.state.client, eng, body.coords, steps=body.steps)
    except osrm.NoRoute:
        raise HTTPException(404, "no route between those points on this engine")
    out = osrm.summarize(data)
    out["engine"] = eng.id
    return out


@router.post("/trip")
async def trip(body: TripIn, request: Request) -> dict:
    """Best order to visit the points (OSRM /trip, a TSP heuristic)."""
    eng = osrm.engine_or_404(body.engine)
    try:
        data = await osrm.trip(request.app.state.client, eng, body.coords, roundtrip=body.roundtrip)
    except osrm.NoRoute:
        raise HTTPException(404, "no trip connects those points on this engine")
    t = data["trips"][0]
    order = sorted(range(len(data["waypoints"])), key=lambda i: data["waypoints"][i]["waypoint_index"])
    out = osrm.summarize({"routes": [t], "waypoints": data["waypoints"]})
    out["order"] = order            # input index in visit order
    out["engine"] = eng.id
    return out
