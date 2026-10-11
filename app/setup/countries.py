"""Natural Earth admin-0 countries (10m), for the isochrone country exclusion mask."""

from __future__ import annotations

from pathlib import Path

from app import config
from app.setup.download import download, extract_zip, fresh_dir

URL = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip"
NE = config.DATA / "naturalearth"
TARGET = NE / "ne_10m_admin_0_countries"


def run(scratch: Path) -> Path:
    if (TARGET / "ne_10m_admin_0_countries.shp").exists():
        print("[skip] Natural Earth countries")
        return TARGET
    zip_path = download(URL, NE / "ne_10m_admin_0_countries.zip", label="ne_10m_admin_0_countries")
    extract_zip(zip_path, fresh_dir(TARGET))
    if not (TARGET / "ne_10m_admin_0_countries.shp").exists():
        raise RuntimeError("unexpected zip layout for ne_10m_admin_0_countries")
    print(f"[done] Natural Earth countries -> {TARGET}")
    return TARGET
