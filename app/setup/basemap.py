"""North America cutout of the Protomaps daily planet build.

    pmtiles extract https://build.protomaps.com/<build>.pmtiles out.pmtiles --bbox=... [--maxzoom=N]

The planet is ~139 GB at z0-15. The NA bbox at z15 is expected around
20-35 GB and takes hours; --maxzoom 10 is ~1 GB and minutes, good enough to
start using the app today.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx

from app import config
from app.setup import pmtiles_cli

BUILDS_JSON = "https://build-metadata.protomaps.dev/builds.json"
BUILD_BASE = "https://build.protomaps.com/"


def pick_build() -> dict:
    builds = httpx.get(BUILDS_JSON, timeout=30).json()
    if config.BASEMAP_BUILD:
        key = f"{config.BASEMAP_BUILD}.pmtiles"
        match = [b for b in builds if b["key"] == key]
        if not match:
            raise RuntimeError(f"pinned build {config.BASEMAP_BUILD} not available; newest: {builds[-1]['key']}")
        return match[0]
    return max(builds, key=lambda b: b["key"])


def output_path(maxzoom: int | None) -> Path:
    name = config.BASEMAP_FILE if maxzoom is None else config.BASEMAP_FILE.replace(".pmtiles", f"-z{maxzoom}.pmtiles")
    return config.BASEMAP_DIR / name


def run(scratch: Path, maxzoom: int | None = None, dry_run: bool = False) -> Path:
    out = output_path(maxzoom)
    if out.exists() and not dry_run:
        print(f"[skip] basemap {out.name} ({out.stat().st_size / 1e9:.1f} GB)")
        return out
    exe = pmtiles_cli.run(scratch)
    build = pick_build()
    src = BUILD_BASE + build["key"]
    w, s, e, n = config.BASEMAP_BBOX
    config.BASEMAP_DIR.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    cmd = [str(exe), "extract", src, str(part), f"--bbox={w},{s},{e},{n}"]
    if maxzoom is not None:
        cmd.append(f"--maxzoom={maxzoom}")
    if dry_run:
        cmd.append("--dry-run")
    print(f"[run]  basemap: build {build['key']} (v{build['version']}, {build['size'] / 1e9:.0f} GB planet)"
          + (f" maxzoom {maxzoom}" if maxzoom is not None else " full zoom"), flush=True)
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    if dry_run:
        return out
    os.replace(part, out)
    (config.BASEMAP_DIR / "BUILD.txt").write_text(json.dumps(
        {"file": out.name, "build": build["key"], "version": build["version"], "bbox": config.BASEMAP_BBOX,
         "maxzoom": maxzoom, "planet_b3sum": build["b3sum"]}, indent=1))
    print(f"[done] basemap -> {out} ({out.stat().st_size / 1e9:.1f} GB)")
    return out
