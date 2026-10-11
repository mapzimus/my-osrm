"""Protomaps basemap fonts (glyph PBFs) and sprites, hosted locally."""

from __future__ import annotations

import shutil
from pathlib import Path

from app import config
from app.setup.download import download, extract_zip, fresh_dir

URL = "https://github.com/protomaps/basemaps-assets/archive/refs/heads/main.zip"
TARGET = config.VENDOR / "basemaps-assets"


def done() -> bool:
    return (TARGET / "fonts" / "Noto Sans Regular" / "0-255.pbf").exists() and \
           (TARGET / "sprites" / "v4" / "light.json").exists()


def run(scratch: Path) -> Path:
    if done():
        print("[skip] basemap assets (fonts + sprites)")
        return TARGET
    zip_path = download(URL, scratch / "basemaps-assets-main.zip", label="basemaps-assets")
    unpacked = extract_zip(zip_path, fresh_dir(scratch / "basemaps-assets"),
                           only_prefixes=("basemaps-assets-main/fonts/", "basemaps-assets-main/sprites/v4/"))
    root = unpacked / "basemaps-assets-main"
    fresh_dir(TARGET)
    shutil.copytree(root / "fonts", TARGET / "fonts")
    shutil.copytree(root / "sprites" / "v4", TARGET / "sprites" / "v4")
    if not done():
        raise RuntimeError("basemaps-assets layout changed; expected fonts/<stack>/0-255.pbf and sprites/v4/light.json")
    n = sum(1 for _ in (TARGET / "fonts").rglob("*.pbf"))
    print(f"[done] basemap assets: {n} glyph files, sprites/v4")
    return TARGET
