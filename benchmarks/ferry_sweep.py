"""Route every kept car ferry dock-to-dock, both directions, on the live engine.

Two bugs survived two audits and the mini-engine test because nothing ever
exercised every ferry on the FULL network: Tsawwassen -> Swartz Bay only
worked one way, and Seattle -> Bremerton lost to the free-flow drive around.
This sweep finds that whole class at once.

    python benchmarks/ferry_sweep.py [--url http://127.0.0.1:5002]

Dock coordinates come from the pipeline's own per-country reports
(data/build/*/ferry-report.json, written by ferry_connectors.py), limited to
the ways kept in data/osrm-na-ferry/ferries/ferry-filter-report.json. No
network access, so it also runs on the cloud build VM. Writes benchmarks/ferry_sweep.md (flagged list) and
benchmarks/ferry_sweep.json (every result).

Flags:
  ONE-WAY     ferry used in one direction only, or one direction > 1.8x slower
  NEVER-USED  neither direction rides a ferry and the road detour is > 2.5x
              the straight line (the ferry is unreachable or never wins)
  NO-ROUTE    OSRM finds no route at all
"""

from __future__ import annotations

import argparse
import json
import math
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "data" / "osrm-na-ferry" / "ferries" / "ferry-filter-report.json"
OUT_MD = Path(__file__).with_name("ferry_sweep.md")
OUT_JSON = Path(__file__).with_name("ferry_sweep.json")
SNAP_SKIP_M = 3_000          # endpoint this far from any road: a mid-water joint, skip


def local_endpoints() -> dict[int, tuple]:
    """way id -> ((lon, lat), (lon, lat)) from the connector reports."""
    ends: dict[int, list] = {}
    for rep_path in sorted((ROOT / "data" / "build").glob("*/ferry-report.json")):
        for e in json.load(open(rep_path, encoding="utf-8"))["endpoints"]:
            pts = ends.setdefault(e["ferry_way"], [])
            if (e["lon"], e["lat"]) not in pts:
                pts.append((e["lon"], e["lat"]))
    return {w: (p[0], p[-1]) for w, p in ends.items() if len(p) >= 2}


def meters(a, b) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    return math.hypot((b[0] - a[0]) * 111_320 * math.cos(lat), (b[1] - a[1]) * 110_540)


def route(url: str, a, b) -> dict | None:
    q = f"{url}/route/v1/driving/{a[0]:.6f},{a[1]:.6f};{b[0]:.6f},{b[1]:.6f}?overview=false&steps=true"
    try:
        with urllib.request.urlopen(q, timeout=60) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        d = json.load(e)
    if d.get("code") != "Ok":
        return None
    r = d["routes"][0]
    ferries = [s["name"] for leg in r["legs"] for s in leg["steps"] if s["mode"] == "ferry"]
    return {"mi": r["distance"] / 1609.344, "min": r["duration"] / 60, "ferries": ferries,
            "snap": max(w["distance"] for w in d["waypoints"])}


def check(url: str, w: dict) -> dict:
    a, b = w["ends"]
    fwd, rev = route(url, a, b), route(url, b, a)
    res = {**w, "fwd": fwd, "rev": rev, "flag": ""}
    if fwd is None or rev is None:
        res["flag"] = "NO-ROUTE"
        return res
    if max(fwd["snap"], rev["snap"]) > SNAP_SKIP_M:
        res["flag"] = "skip (end in open water)"
        return res
    straight_mi = meters(a, b) / 1609.344
    used = (bool(fwd["ferries"]), bool(rev["ferries"]))
    slow, fast = max(fwd["min"], rev["min"]), min(fwd["min"], rev["min"])
    if used[0] != used[1] or (fast > 20 and slow / fast > 1.8):
        res["flag"] = "ONE-WAY"
    elif not any(used) and straight_mi > 0.5 and min(fwd["mi"], rev["mi"]) > 2.5 * straight_mi:
        res["flag"] = "NEVER-USED"
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5002")
    args = ap.parse_args()

    kept = [r for r in json.load(open(REPORT, encoding="utf-8")) if r["keep"]]
    ends = local_endpoints()
    print(f"{len(kept)} kept ferry ways, {sum(r['way'] in ends for r in kept)} with dock coordinates", flush=True)
    work = [{"way": r["way"], "name": r["name"], "why": r["why"], "ends": ends[r["way"]]}
            for r in kept if r["way"] in ends]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda w: check(args.url, w), work))
    OUT_JSON.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")

    flagged = [r for r in results if r["flag"] in ("ONE-WAY", "NEVER-USED", "NO-ROUTE")]
    counts = {f: sum(r["flag"] == f for r in results) for f in ("ONE-WAY", "NEVER-USED", "NO-ROUTE")}
    lines = [f"# Ferry sweep: {len(results)} kept car ferries, both directions", "",
             f"ONE-WAY {counts['ONE-WAY']} · NEVER-USED {counts['NEVER-USED']} · "
             f"NO-ROUTE {counts['NO-ROUTE']} · skipped (mid-water joints) "
             f"{sum(r['flag'].startswith('skip') for r in results)}", "",
             "| Flag | Ferry | Way | A→B | B→A |", "|---|---|---|---|---|"]

    def cell(x):
        return "—" if x is None else f"{x['mi']:.0f} mi {x['min']:.0f} min{' ⛴' if x['ferries'] else ''}"
    for r in sorted(flagged, key=lambda r: (r["flag"], r["name"])):
        lines.append(f"| {r['flag']} | {r['name'] or '(unnamed)'} | w{r['way']} | {cell(r['fwd'])} | {cell(r['rev'])} |")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:3]))
    print(f"wrote {OUT_MD.name}, {OUT_JSON.name}")


if __name__ == "__main__":
    main()
