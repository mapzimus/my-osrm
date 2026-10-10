"""Route report card: score an OSRM engine against benchmarks/routes.toml.

Usage:
    python benchmarks/report_card.py                       # US engine on :5000
    python benchmarks/report_card.py --url http://127.0.0.1:5001 --engine na

Stdlib only, so it runs with any Python 3.11+ (tomllib).
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
import urllib.request
from pathlib import Path

METERS_PER_MILE = 1609.344
ROUTES_FILE = Path(__file__).with_name("routes.toml")


def route(url: str, src: list[float], dst: list[float]) -> tuple[float, float, bool]:
    """Return (miles, hours, used_ferry) for the fastest route."""
    coords = f"{src[0]},{src[1]};{dst[0]},{dst[1]}"
    req = f"{url}/route/v1/driving/{coords}?overview=false&steps=true"
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if data.get("code") != "Ok":
        raise RuntimeError(data.get("message", data.get("code")))
    r = data["routes"][0]
    used_ferry = any(
        step.get("mode") == "ferry" for leg in r["legs"] for step in leg["steps"]
    )
    return r["distance"] / METERS_PER_MILE, r["duration"] / 3600, used_ferry


def grade(case: dict, miles: float, hours: float, used_ferry: bool, engine: str) -> str:
    """Return "PASS", "FAIL", or "SKIP" for one case.

    case["max_miles"] is the shortcut threshold; case["kind"] is
    "ferry" | "border" | "control"; case["needs"] is "us" | "na".
    """
    # A US-only engine can't enter Canada by design; grading it on that
    # would mix "broken" with "out of scope". Still printed, just not scored.
    if case["needs"] == "na" and engine != "na":
        return "SKIP"
    if miles > case["max_miles"]:
        return "FAIL"
    # Ferry cases need proof: a short distance alone could be a fluke.
    if case["kind"] == "ferry" and not used_ferry:
        return "FAIL"
    # Implausibly short is also broken (bad snap, missing geometry).
    if miles < case["max_miles"] * 0.3:
        return "FAIL"
    # Right road, wrong clock: drive time outside the real-world band.
    if "hours" in case and not case["hours"][0] <= hours <= case["hours"][1]:
        return "FAIL"
    return "PASS"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:5000")
    ap.add_argument("--engine", choices=["us", "na"], default="us")
    args = ap.parse_args()

    cases = tomllib.loads(ROUTES_FILE.read_text())["case"]
    passed = scored = 0
    print(f"\nReport card: {args.url} (engine={args.engine})\n")
    for case in cases:
        try:
            miles, hours, ferry = route(args.url, case["from"], case["to"])
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"  ERROR {case['name']}: {exc}")
            scored += 1
            continue
        result = grade(case, miles, hours, ferry, args.engine)
        if result != "SKIP":
            scored += 1
            passed += result == "PASS"
        tag = " ferry" if ferry else ""
        print(f"  {result:4}  {miles:6.0f} mi {hours:5.1f} h{tag:6}  {case['name']}")
    print(f"\nScore: {passed}/{scored}\n")
    return 0 if passed == scored else 1


if __name__ == "__main__":
    sys.exit(main())
