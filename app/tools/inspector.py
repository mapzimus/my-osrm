"""Network inspector: what did the engine snap to, and what did it think of that road?"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app import config, osrm
from app.registry import ToolMeta

meta = ToolMeta(id="inspector", title="Inspect", order=60, icon="🔍")
router = APIRouter()

PROBE_M = 120     # a tiny route out of the snapped point reveals the road's name/ref/mode


@router.get("/nearest")
async def nearest(request: Request, lon: float, lat: float, engine: str | None = None, number: int = 3) -> dict:
    eng = osrm.engine_or_404(engine)
    client = request.app.state.client
    try:
        data = await osrm.nearest(client, eng, lon, lat, number=max(1, min(number, 10)))
    except osrm.NoRoute:
        raise HTTPException(404, "nothing nearby on this engine")
    wps = [{"location": w["location"], "name": w.get("name", ""), "snap_m": w.get("distance", 0),
            "nodes": w.get("nodes", []), "hint": w.get("hint", "")} for w in data["waypoints"]]
    best = wps[0]
    # Probe: route from the snapped point to a point ~PROBE_M east; the first
    # step tells us the way's name/ref/mode and the implied speed.
    probe = None
    try:
        dx = PROBE_M / 111_320 / max(0.2, abs(__import__("math").cos(__import__("math").radians(lat))))
        r = await osrm.route(client, eng, [best["location"], [best["location"][0] + dx, best["location"][1]]], steps=True)
        s = osrm.summarize(r)
        first = s["steps"][0] if s["steps"] else None
        if first:
            kmh = (first["distance_m"] / first["duration_s"] * 3.6) if first["duration_s"] else None
            probe = {"name": first["name"], "ref": first["ref"], "mode": first["mode"],
                     "kmh": None if kmh is None else round(kmh, 1), "mph": None if kmh is None else round(kmh / 1.609, 1),
                     "distance_m": round(s["miles"] * osrm.METERS_PER_MILE), "hours": s["hours"]}
    except (osrm.NoRoute, HTTPException):
        pass
    return {"engine": eng.id, "query": [lon, lat], "waypoints": wps, "probe": probe}


@router.get("/ferries.geojson")
def ferries() -> FileResponse:
    path = config.APP_DATA / "ferries.geojson"
    if not path.exists():
        raise HTTPException(404, "no ferry overlay yet: run  python -m app.setup --only ferries")
    return FileResponse(path, media_type="application/geo+json")
