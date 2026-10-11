"""Report card on the map: run benchmarks/routes.toml against an engine as a job."""

from __future__ import annotations

import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import config, jobs, osrm
from app.registry import ToolMeta

sys.path.insert(0, str(config.BENCHMARKS_DIR))
import report_card  # noqa: E402  (benchmarks/report_card.py: route(), grade())

meta = ToolMeta(id="benchmarks", title="Report card", order=70, icon="✓")
router = APIRouter()


def load_cases() -> list[dict]:
    path = getattr(router, "routes_file", None) or report_card.ROUTES_FILE
    cases = tomllib.loads(path.read_text(encoding="utf-8"))["case"]
    for i, c in enumerate(cases):
        c["index"] = i
    return cases


@router.get("/cases")
def cases() -> list[dict]:
    return load_cases()


class RunIn(BaseModel):
    engine: str | None = None


def run_case(url: str, scope: str, case: dict) -> dict:
    out = {"index": case["index"], "name": case["name"], "kind": case["kind"], "needs": case["needs"],
           "from": case["from"], "to": case["to"], "note": case.get("note", ""), "max_miles": case.get("max_miles"),
           "band": case.get("hours")}
    try:
        miles, hours, ferry, snap_m = report_card.route(url, case["from"], case["to"])
    except report_card.NoRoute:
        out.update(result="PASS" if case["kind"] == "unreachable" else "FAIL", status="no route")
        return out
    except Exception as exc:  # noqa: BLE001
        out.update(result="ERROR", status=str(exc))
        return out
    out.update(result=report_card.grade(case, miles, hours, ferry, scope, snap_m), status="ok",
               miles=miles, hours=hours, used_ferry=ferry, snap_m=snap_m)
    return out


def with_geometry(url: str, rows: list[dict]) -> None:
    """Fetch the drawn line for each routed case (second pass, overview=simplified)."""
    import json
    import urllib.request

    def fetch(r: dict) -> None:
        if r.get("status") != "ok":
            return
        coords = f"{r['from'][0]},{r['from'][1]};{r['to'][0]},{r['to'][1]}"
        req = f"{url}/route/v1/driving/{coords}?overview=simplified&geometries=geojson&steps=false"
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                r["geometry"] = json.load(resp)["routes"][0]["geometry"]
        except Exception:  # noqa: BLE001
            r["geometry"] = None

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, rows))


@router.post("/run")
def run(body: RunIn, request: Request) -> dict:
    eng = osrm.engine_or_404(body.engine)
    cases_ = load_cases()

    def work(job: jobs.Job) -> dict:
        rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for i, row in enumerate(pool.map(lambda c: run_case(eng.url, eng.scope, c), cases_)):
                rows.append(row)
                job.update((i + 1) / (len(cases_) + 2), f"{i + 1}/{len(cases_)} {row['name']}")
        job.update(message="fetching route lines")
        with_geometry(eng.url, rows)
        scored = [r for r in rows if r["result"] in ("PASS", "FAIL", "ERROR")]
        passed = sum(r["result"] == "PASS" for r in scored)
        return {"engine": eng.id, "scope": eng.scope, "passed": passed, "scored": len(scored), "rows": rows}

    job = request.app.state.jobs.submit("benchmarks", work)
    return job.public()
