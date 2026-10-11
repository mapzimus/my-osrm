"""Export: GPX from a route GeoJSON. (PNG and GeoJSON exports are done in the browser.)"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.registry import ToolMeta

meta = ToolMeta(id="export", title="Export", order=40, needs_engine=False, icon="⤓")
router = APIRouter()


class GpxIn(BaseModel):
    name: str = "my-osrm route"
    geometry: dict                      # GeoJSON LineString
    waypoints: list[dict] = []          # [{location: [lon, lat], name}]


def to_gpx(name: str, geometry: dict, waypoints: list[dict]) -> bytes:
    if geometry.get("type") != "LineString":
        raise HTTPException(422, "geometry must be a GeoJSON LineString")
    gpx = ET.Element("gpx", version="1.1", creator="my-osrm", xmlns="http://www.topografix.com/GPX/1/1")
    md = ET.SubElement(gpx, "metadata")
    ET.SubElement(md, "name").text = name
    ET.SubElement(md, "time").text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i, w in enumerate(waypoints):
        lon, lat = w["location"]
        wpt = ET.SubElement(gpx, "wpt", lat=f"{lat:.6f}", lon=f"{lon:.6f}")
        ET.SubElement(wpt, "name").text = w.get("name") or f"Stop {i + 1}"
    trk = ET.SubElement(gpx, "trk")
    ET.SubElement(trk, "name").text = name
    seg = ET.SubElement(trk, "trkseg")
    for lon, lat in geometry["coordinates"]:
        ET.SubElement(seg, "trkpt", lat=f"{lat:.6f}", lon=f"{lon:.6f}")
    return ET.tostring(gpx, encoding="utf-8", xml_declaration=True)


@router.post("/gpx")
def gpx(body: GpxIn) -> Response:
    data = to_gpx(body.name, body.geometry, body.waypoints)
    fname = "".join(c if c.isalnum() or c in "-_ " else "_" for c in body.name).strip() or "route"
    return Response(data, media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.gpx"'})
