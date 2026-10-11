"""python -m app.setup [--only a,b] [--maxzoom N] [--dry-run]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app import config

STAGES = ["vendor", "cli", "assets", "basemap", "countries", "ferries", "places"]


def main() -> int:
    ap = argparse.ArgumentParser(description="One-time setup for the offline map app")
    ap.add_argument("--only", help=f"comma list of stages: {', '.join(STAGES)}")
    ap.add_argument("--maxzoom", type=int, default=None,
                    help="basemap max zoom (10 = ~1 GB quick; omit for full z15, ~hours)")
    ap.add_argument("--dry-run", action="store_true", help="basemap: estimate size only")
    args = ap.parse_args()
    stages = args.only.split(",") if args.only else STAGES
    bad = [s for s in stages if s not in STAGES]
    if bad:
        ap.error(f"unknown stage(s) {bad}; choose from {STAGES}")

    scratch = config.DATA / "scratch" / "app-setup"
    scratch.mkdir(parents=True, exist_ok=True)
    for stage in stages:
        print(f"\n=== {stage} ===", flush=True)
        if stage == "vendor":
            from app.setup import vendor; vendor.run(scratch)
        elif stage == "cli":
            from app.setup import pmtiles_cli; pmtiles_cli.run(scratch)
        elif stage == "assets":
            from app.setup import assets; assets.run(scratch)
        elif stage == "basemap":
            from app.setup import basemap; basemap.run(scratch, maxzoom=args.maxzoom, dry_run=args.dry_run)
        elif stage == "countries":
            from app.setup import countries; countries.run(scratch)
        elif stage == "ferries":
            from app.setup import ferries; ferries.run(scratch)
        elif stage == "places":
            from app.setup import places; places.run(scratch)
    print("\nsetup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
