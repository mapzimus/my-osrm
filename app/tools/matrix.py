"""N x N drive-time / distance matrix (OSRM /table)."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import osrm
from app.registry import ToolMeta

meta = ToolMeta(id="matrix", title="Matrix", order=30, icon="⊞")
router = APIRouter()

MAX_POINTS = 400


class MatrixIn(BaseModel):
    engine: str | None = None
    coords: list[list[float]] = Field(min_length=2, max_length=MAX_POINTS)
    names: list[str] | None = None


async def compute(body: MatrixIn, request: Request) -> dict:
    eng = osrm.engine_or_404(body.engine)
    names = body.names or [f"P{i + 1}" for i in range(len(body.coords))]
    if len(names) != len(body.coords):
        raise HTTPException(422, "names must match coords")
    try:
        data = await osrm.table(request.app.state.client, eng, body.coords)
    except osrm.NoRoute:
        raise HTTPException(404, "no routes on this engine")
    hours = [[None if d is None else d / 3600 for d in row] for row in data["durations"]]
    miles = [[None if d is None else d / osrm.METERS_PER_MILE for d in row] for row in data.get("distances") or [[None] * len(names)] * len(names)]
    snaps = [w.get("distance", 0) for w in data.get("destinations", [])]
    return {"engine": eng.id, "names": names, "hours": hours, "miles": miles, "snap_m": snaps,
            "coords": body.coords}


@router.post("")
async def matrix(body: MatrixIn, request: Request) -> dict:
    return await compute(body, request)


@router.post("/csv")
async def matrix_csv(body: MatrixIn, request: Request, what: str = "hours") -> PlainTextResponse:
    m = await compute(body, request)
    grid = m["hours"] if what == "hours" else m["miles"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([f"{what} ({m['engine']})", *m["names"]])
    for name, row in zip(m["names"], grid):
        w.writerow([name, *("" if v is None else f"{v:.2f}" for v in row)])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="matrix_{what}.csv"'})
