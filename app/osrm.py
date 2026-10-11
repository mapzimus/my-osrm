"""Thin async client for the OSRM HTTP API. All engine traffic goes through
here so tests can swap the transport and every tool shares one engine picker.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import HTTPException

from app import config

METERS_PER_MILE = 1609.344


class NoRoute(Exception):
    pass


def engine_or_404(engine_id: str | None) -> config.Engine:
    eng = config.ENGINES.get(engine_id or config.DEFAULT_ENGINE)
    if eng is None:
        raise HTTPException(404, f"unknown engine {engine_id!r}; known: {', '.join(config.ENGINES)}")
    return eng


def fmt(coords: list[list[float]]) -> str:
    return ";".join(f"{x:.6f},{y:.6f}" for x, y in coords)


async def _get(client: httpx.AsyncClient, url: str, params: dict | None = None, timeout: float = 60) -> dict:
    try:
        r = await client.get(url, params=params, timeout=timeout)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"engine unreachable: {url.split('/route')[0].split('/table')[0]} ({exc.__class__.__name__})")
    try:
        data = r.json()
    except ValueError:
        raise HTTPException(502, f"engine returned non-JSON (HTTP {r.status_code})")
    if data.get("code") == "NoRoute":
        raise NoRoute()
    if data.get("code") != "Ok":
        raise HTTPException(502, f"engine error: {data.get('message') or data.get('code')}")
    return data


async def route(client: httpx.AsyncClient, eng: config.Engine, coords: list[list[float]],
                steps: bool = True, overview: str = "full") -> dict:
    url = f"{eng.url}/route/v1/driving/{fmt(coords)}"
    params = {"overview": overview, "geometries": "geojson", "steps": str(steps).lower(), "annotations": "false"}
    return await _get(client, url, params)


async def table(client: httpx.AsyncClient, eng: config.Engine, coords: list[list[float]],
                sources: list[int] | None = None) -> dict:
    url = f"{eng.url}/table/v1/driving/{fmt(coords)}"
    params: dict[str, Any] = {"annotations": "duration,distance"}
    if sources is not None:
        params["sources"] = ";".join(map(str, sources))
    return await _get(client, url, params, timeout=180)


async def nearest(client: httpx.AsyncClient, eng: config.Engine, lon: float, lat: float, number: int = 1) -> dict:
    url = f"{eng.url}/nearest/v1/driving/{lon:.6f},{lat:.6f}"
    return await _get(client, url, {"number": number})


async def trip(client: httpx.AsyncClient, eng: config.Engine, coords: list[list[float]], roundtrip: bool = True) -> dict:
    url = f"{eng.url}/trip/v1/driving/{fmt(coords)}"
    params = {"overview": "full", "geometries": "geojson", "steps": "true",
              "roundtrip": str(roundtrip).lower(), "source": "first", "destination": "last" if not roundtrip else "any"}
    return await _get(client, url, params, timeout=180)


async def health(client: httpx.AsyncClient, eng: config.Engine, timeout: float = 1.5) -> bool:
    try:
        r = await client.get(f"{eng.url}/route/v1/driving/-77.0,38.9;-77.0,38.9", params={"overview": "false"}, timeout=timeout)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


async def healthy_engines(client: httpx.AsyncClient) -> list[config.Engine]:
    engs = list(config.ENGINES.values())
    ok = await asyncio.gather(*(health(client, e) for e in engs))
    return [e for e, h in zip(engs, ok) if h]


def summarize(route_json: dict) -> dict:
    """Route -> the bits the UI shows: miles, hours, ferry use, geometry, turn list."""
    r = route_json["routes"][0]
    steps = []
    for li, leg in enumerate(r["legs"]):
        for s in leg.get("steps", []):
            m = s.get("maneuver", {})
            steps.append({
                "leg": li, "name": s.get("name", ""), "ref": s.get("ref", ""), "mode": s.get("mode", "driving"),
                "type": m.get("type", ""), "modifier": m.get("modifier", ""),
                "distance_m": s.get("distance", 0), "duration_s": s.get("duration", 0),
                "location": m.get("location"),
            })
    used_ferry = any(s["mode"] == "ferry" for s in steps)
    return {
        "miles": r["distance"] / METERS_PER_MILE,
        "km": r["distance"] / 1000,
        "hours": r["duration"] / 3600,
        "used_ferry": used_ferry,
        "geometry": r["geometry"],
        "legs": [{"miles": l["distance"] / METERS_PER_MILE, "hours": l["duration"] / 3600} for l in r["legs"]],
        "waypoints": [{"location": w["location"], "name": w.get("name", ""), "snap_m": w.get("distance", 0)}
                      for w in route_json.get("waypoints", [])],
        "steps": steps,
    }
