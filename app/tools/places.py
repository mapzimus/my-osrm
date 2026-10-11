"""Offline place search over data/app/places.sqlite (built by app/setup/places.py)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

from app import config
from app.registry import ToolMeta

meta = ToolMeta(id="places", title="Places", order=90, needs_engine=False, icon="⌕")
router = APIRouter()

DB = config.APP_DATA / "places.sqlite"


def db_path():
    return getattr(router, "db_path", None) or DB


def fts_query(q: str) -> str:
    # each word as a prefix term; quotes guard punctuation (St. John's)
    words = [w.replace('"', "") for w in q.split() if w.strip()]
    return " ".join(f'"{w}"*' for w in words)


@router.get("")
def search(q: str, limit: int = 10) -> list[dict]:
    path = db_path()
    if not path.exists():
        raise HTTPException(503, "no place index: run  python -m app.setup --only places")
    q = q.strip()
    if len(q) < 2:
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """SELECT p.name, p.region, p.country, p.kind, p.population, p.lon, p.lat
               FROM places_fts f JOIN places p ON p.id = f.rowid
               WHERE places_fts MATCH ?
               ORDER BY p.population DESC, p.name LIMIT ?""",
            (fts_query(q), max(1, min(limit, 50)))).fetchall()
    except sqlite3.OperationalError as exc:
        raise HTTPException(400, f"bad query: {exc}")
    finally:
        con.close()
    return [{"name": n, "region": r, "country": c, "kind": k, "population": pop, "lon": lon, "lat": lat,
             "label": ", ".join(x for x in (n, r, c) if x)}
            for n, r, c, k, pop, lon, lat in rows]
