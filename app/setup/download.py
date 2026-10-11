"""Resumable HTTP download with checksum verification, plus safe archive extraction."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import tarfile
import time
import zipfile
from pathlib import Path

import httpx

CHUNK = 1 << 20


def _hash(path: Path, algo: str) -> bytes:
    h = hashlib.new(algo)
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.digest()


def verify(path: Path, sha256: str | None = None, integrity: str | None = None) -> None:
    """sha256: hex digest. integrity: npm SRI string like 'sha512-<base64>'."""
    if sha256:
        got = _hash(path, "sha256").hex()
        if got != sha256.lower():
            raise RuntimeError(f"{path.name}: sha256 mismatch\n  want {sha256}\n  got  {got}")
    if integrity:
        algo, _, b64 = integrity.partition("-")
        got = base64.b64encode(_hash(path, algo)).decode()
        if got != b64:
            raise RuntimeError(f"{path.name}: {algo} mismatch (npm integrity)")


def download(url: str, dest: Path, sha256: str | None = None, integrity: str | None = None,
             label: str | None = None, retries: int = 5) -> Path:
    """Stream url -> dest. Resumes dest.part with a Range request; verifies; renames."""
    if dest.exists():
        print(f"[skip] {label or dest.name}: exists")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(retries):
        try:
            have = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={have}-"} if have else {}
            with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as r:
                if r.status_code == 416:            # already complete
                    break
                if have and r.status_code != 206:    # server ignored Range: start over
                    have = 0
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0)) + have
                mode = "ab" if have else "wb"
                t0, done, shown = time.time(), have, -1
                with part.open(mode) as f:
                    for chunk in r.iter_bytes(CHUNK):
                        f.write(chunk)
                        done += len(chunk)
                        pct = int(100 * done / total) if total else -1
                        if pct != shown and (pct % 5 == 0 or done == total):
                            rate = (done - have) / max(time.time() - t0, 1e-6) / 1e6
                            print(f"  {label or dest.name}: {done / 1e6:,.0f} MB"
                                  + (f" ({pct}%)" if total else "") + f"  {rate:.1f} MB/s", flush=True)
                            shown = pct
            break
        except (httpx.HTTPError, OSError) as exc:
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt + 1}/{retries} after {exc.__class__.__name__}: {exc}", flush=True)
            time.sleep(3 * (attempt + 1))
    verify(part, sha256=sha256, integrity=integrity)
    os.replace(part, dest)
    return dest


def fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def extract_tar(archive: Path, into: Path) -> Path:
    with tarfile.open(archive, "r:*") as tf:
        tf.extractall(into, filter="data")
    return into


def extract_zip(archive: Path, into: Path, only_prefixes: tuple[str, ...] = ()) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for m in zf.infolist():
            name = m.filename
            if ".." in name.split("/") or name.startswith("/"):
                raise RuntimeError(f"unsafe path in zip: {name}")
            if only_prefixes and not any(name.startswith(p) for p in only_prefixes):
                continue
            zf.extract(m, into)
    return into
