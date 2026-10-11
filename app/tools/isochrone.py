"""Drive-time isochrones as a background job, wrapping experiments/isochrone/isochrone.py."""

from __future__ import annotations

import base64
import shutil
import sys

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import config, jobs, osrm
from app.registry import ToolMeta

sys.path.insert(0, str(config.ISOCHRONE_DIR))
import isochrone as iso  # noqa: E402

meta = ToolMeta(id="isochrone", title="Isochrone", order=20, icon="◎")
router = APIRouter()

PRESETS = {k: {"bbox": v[0], "step": v[1], "hours": v[2], "upsample": v[3]} for k, v in iso.REGIONS.items()}


class IsoIn(BaseModel):
    engine: str | None = None
    name: str = "Origin"
    origin: list[float] = Field(min_length=2, max_length=2)
    region: str | None = None                       # preset key, or null with bbox+step
    bbox: list[float] | None = None                 # west, south, east, north
    step: float | None = Field(default=None, gt=0.005, le=1.0)
    hours: int | None = Field(default=None, ge=1, le=200)   # None/0 = auto
    fill: bool = True                               # Natural Earth land fill (needs Docker)
    flights: bool = False


@router.get("/presets")
def presets() -> dict:
    return {"regions": PRESETS, "docker": shutil.which("docker") is not None}


@router.post("/jobs")
def start(body: IsoIn, request: Request) -> dict:
    eng = osrm.engine_or_404(body.engine)
    if body.region:
        if body.region not in iso.REGIONS:
            raise HTTPException(422, f"unknown region; presets: {', '.join(iso.REGIONS)}")
        region: str | tuple = body.region
    else:
        if not body.bbox or body.step is None:
            raise HTTPException(422, "give a preset region or bbox + step")
        w, s, e, n = body.bbox
        if not (w < e and s < n):
            raise HTTPException(422, "bbox must be west,south,east,north")
        cells = ((e - w) / body.step) * ((n - s) / body.step)
        if cells > 1_500_000:
            raise HTTPException(422, f"grid too fine: {cells:,.0f} cells (max 1.5M); raise step")
        region = (tuple(body.bbox), body.step)
    origin = (float(body.origin[0]), float(body.origin[1]))
    fill = body.fill and shutil.which("docker") is not None

    def work(job: jobs.Job) -> dict:
        def progress(done: int, total: int) -> None:
            job.update(0.05 + 0.85 * done / max(total, 1), f"{done}/{total} table requests")
        job.update(0.02, "building grid")
        payload = iso.build_payload(eng.url, body.name, origin, region, max_hours=body.hours or None,
                                    fill=fill, progress=progress, flights_on=body.flights)
        job.update(0.95, "rendering")
        png = base64.b64decode(payload.pop("png"))
        job.png = png                               # served separately, not through JSON
        payload["engine"] = eng.id
        payload["png_url"] = f"/api/isochrone/jobs/{job.id}/image.png"
        return payload

    job = request.app.state.jobs.submit("isochrone", work)
    return job.public()


@router.get("/jobs/{job_id}/image.png")
def image(job_id: str, request: Request) -> Response:
    job = request.app.state.jobs.get(job_id)
    png = getattr(job, "png", None)
    if job.status != "done" or png is None:
        raise HTTPException(404, "no image for this job")
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})
