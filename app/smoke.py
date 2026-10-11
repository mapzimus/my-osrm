"""End-to-end smoke test against a real engine through the app's API.

    python -m app                                  # in another terminal
    python app/smoke.py --app http://127.0.0.1:8080 --engine na-ferry

Checks: engine health, three report-card trips, /nearest on a ferry dock,
a small custom isochrone job to completion. Exit code 0 = all good.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

BOS = [-71.0589, 42.3601]
GRAND_ISLE = [-73.3510, 44.7206]
PLATTSBURGH = [-73.4529, 44.6995]


def wait(client: httpx.Client, job_id: str, timeout: float) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}", params={"result": "true"}).json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        print(f"    {j['status']} {int(100 * j['progress'])}% {j['message']}", end="\r", flush=True)
        time.sleep(1)
    raise TimeoutError(job_id)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="http://127.0.0.1:8080")
    ap.add_argument("--engine", default="na-ferry")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.app, timeout=120)
    ok = True

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= cond
        print(f"  {'PASS' if cond else 'FAIL'}  {label}  {detail}")

    engines = {e["id"]: e for e in c.get("/api/engines").json()}
    check("engine up", engines.get(args.engine, {}).get("healthy", False), args.engine)
    if not ok:
        return 1

    r = c.post("/api/route", json={"engine": args.engine, "coords": [GRAND_ISLE, PLATTSBURGH]}).json()
    check("Lake Champlain ferry route", r.get("used_ferry") is True and r.get("miles", 99) < 25,
          f"{r.get('miles', 0):.0f} mi, {r.get('hours', 0):.2f} h, ferry={r.get('used_ferry')}")

    r = c.post("/api/route", json={"engine": args.engine, "coords": [BOS, [-77.0365, 38.8977]]}).json()
    check("Boston -> DC drive time 6.5-8.5 h", 6.5 <= r.get("hours", 0) <= 8.5, f"{r.get('hours', 0):.2f} h")

    m = c.post("/api/matrix", json={"engine": args.engine, "coords": [BOS, PLATTSBURGH, GRAND_ISLE]}).json()
    check("3x3 matrix", len(m.get("hours", [])) == 3 and m["hours"][0][0] == 0)

    n = c.get("/api/inspector/nearest", params={"engine": args.engine, "lon": -73.3510, "lat": 44.7206}).json()
    check("nearest at Grand Isle dock", bool(n.get("waypoints")), n.get("waypoints", [{}])[0].get("name", ""))

    cmp_ = c.post("/api/compare", json={"coords": [GRAND_ISLE, PLATTSBURGH]}).json()
    check("compare returns the engine", any(x["engine"] == args.engine for x in cmp_["results"]))

    job = c.post("/api/benchmarks/run", json={"engine": args.engine}).json()
    res = wait(c, job["id"], 300)
    print()
    check("report card job", res["status"] == "done", f"{res.get('result', {}).get('passed')}/{res.get('result', {}).get('scored')}")

    job = c.post("/api/isochrone/jobs", json={"engine": args.engine, "name": "Smoke", "origin": BOS,
                                               "bbox": [-72.5, 41.5, -70.0, 43.5], "step": 0.1, "hours": 2, "fill": False}).json()
    res = wait(c, job["id"], 300)
    print()
    img = c.get(res.get("result", {}).get("png_url", "/nope"))
    check("isochrone job + PNG", res["status"] == "done" and img.status_code == 200, res.get("error") or "")

    print("\nALL PASS" if ok else "\nFAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
