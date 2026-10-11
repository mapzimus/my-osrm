"""Vendor the front-end libraries from npm (no node_modules, no bundler).

Each pinned package's tarball is fetched from the npm registry, verified
against the registry's own `dist.integrity`, and the runtime files copied to
app/web/vendor/<name>/. index.html maps bare specifiers to those paths with an
import map. `scan_bare_imports` catches a vendored file importing something
the import map doesn't cover (pmtiles -> fflate is the known one).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import httpx

from app import config
from app.setup.download import download, extract_tar, fresh_dir

# package -> (subdirectory of the tarball to copy, keep predicate)
KEEP = {
    "maplibre-gl": ("dist", lambda p: p.suffix in (".js", ".mjs", ".css") and "-dev" not in p.name),
    "pmtiles": ("dist", lambda p: p.suffix in (".js", ".mjs")),
    "fflate": ("esm", lambda p: p.suffix in (".js", ".mjs")),
    "@protomaps/basemaps": ("dist/esm", lambda p: p.suffix in (".js", ".mjs")),
}

# bare specifier -> served path; must cover every bare import in vendored code
IMPORT_MAP = {
    "maplibre-gl": "/vendor/maplibre-gl/maplibre-gl.mjs",
    "pmtiles": "/vendor/pmtiles/esm/index.js",
    "fflate": "/vendor/fflate/browser.js",
    "@protomaps/basemaps": "/vendor/protomaps-basemaps/index.js",
}


def folder_for(pkg: str) -> str:
    return pkg.lstrip("@").replace("/", "-")


def registry_meta(pkg: str, version: str) -> dict:
    url = f"https://registry.npmjs.org/{pkg.replace('/', '%2F')}/{version}"
    r = httpx.get(url, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def vendor_package(pkg: str, version: str, scratch: Path) -> dict:
    target = config.VENDOR / folder_for(pkg)
    stamp = target / ".version"
    if stamp.exists() and stamp.read_text().strip() == version:
        print(f"[skip] vendor {pkg}@{version}")
        return {"version": version, "files": sorted(p.name for p in target.rglob("*") if p.is_file())}
    meta = registry_meta(pkg, version)
    tgz = download(meta["dist"]["tarball"], scratch / f"{folder_for(pkg)}-{version}.tgz",
                   integrity=meta["dist"]["integrity"], label=f"{pkg}@{version}")
    unpacked = extract_tar(tgz, fresh_dir(scratch / folder_for(pkg)))
    sub, keep = KEEP[pkg]
    src = unpacked / "package" / sub
    if not src.exists():
        raise RuntimeError(f"{pkg}: expected {sub}/ in tarball")
    fresh_dir(target)
    files = []
    for p in src.rglob("*"):
        if p.is_file() and keep(p):
            rel = p.relative_to(src)
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target / rel)
            files.append(str(rel).replace("\\", "/"))
    stamp.write_text(version)
    print(f"[done] vendor {pkg}@{version}: {len(files)} files")
    return {"version": version, "files": sorted(files)}


BARE = re.compile(r"""(?:^|[;\s])(?:import|export)\s*(?:[\w*{}\s,]*\s*from\s*)?["']([^./"'][^"']*)["']""", re.M)
DYN = re.compile(r"""import\(\s*["']([^./"'][^"']*)["']\s*\)""")


def scan_bare_imports(vendor_dir: Path = config.VENDOR) -> dict[str, list[str]]:
    """Bare specifiers in vendored JS that the import map doesn't cover."""
    missing: dict[str, list[str]] = {}
    for js in vendor_dir.rglob("*.js"):
        if js.parent.name == "basemaps-assets":
            continue
        text = js.read_text(encoding="utf-8", errors="replace")
        for spec in set(BARE.findall(text)) | set(DYN.findall(text)):
            if spec not in IMPORT_MAP:
                missing.setdefault(str(js.relative_to(vendor_dir)).replace("\\", "/"), []).append(spec)
    return missing


def run(scratch: Path) -> None:
    config.VENDOR.mkdir(parents=True, exist_ok=True)
    manifest = {pkg: vendor_package(pkg, ver, scratch) for pkg, ver in config.NPM_PINS.items()}
    for spec, path in IMPORT_MAP.items():
        if not (config.WEB / path.lstrip("/")).exists():
            raise RuntimeError(f"import map target missing for {spec}: {path}")
    missing = scan_bare_imports()
    if missing:
        raise RuntimeError(f"vendored code imports specifiers not in IMPORT_MAP: {missing}")
    manifest["import_map"] = IMPORT_MAP
    (config.VENDOR / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
