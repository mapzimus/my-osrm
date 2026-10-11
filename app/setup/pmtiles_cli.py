"""go-pmtiles CLI (pmtiles.exe) for `pmtiles extract`."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

import httpx

from app import config
from app.setup.download import download, extract_zip, fresh_dir

EXE = config.BIN / ("pmtiles.exe" if platform.system() == "Windows" else "pmtiles")


def asset_suffix() -> str:
    sys_ = platform.system()
    arch = platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}[arch]
    return f"_{sys_}_{arch}.zip"


def run(scratch: Path) -> Path:
    if EXE.exists():
        out = subprocess.run([str(EXE), "version"], capture_output=True, text=True)
        print(f"[skip] pmtiles CLI: {out.stdout.strip() or out.stderr.strip()}")
        return EXE
    tag = f"v{config.GO_PMTILES_VERSION}"
    r = httpx.get(f"https://api.github.com/repos/protomaps/go-pmtiles/releases/tags/{tag}",
                  timeout=30, headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    suffix = asset_suffix()
    asset = next(a for a in r.json()["assets"] if a["name"].endswith(suffix))
    digest = (asset.get("digest") or "").removeprefix("sha256:") or None
    zip_path = download(asset["browser_download_url"], scratch / asset["name"], sha256=digest, label="go-pmtiles")
    unpacked = extract_zip(zip_path, fresh_dir(scratch / "go-pmtiles"))
    exe = next(p for p in unpacked.rglob("pmtiles*") if p.is_file() and p.suffix in (".exe", ""))
    config.BIN.mkdir(parents=True, exist_ok=True)
    exe.replace(EXE)
    print(f"[done] pmtiles CLI -> {EXE}")
    return EXE
