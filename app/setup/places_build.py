"""Build data/app/places.sqlite from place=city/town/village nodes.

Runs INSIDE the my-osrm-tools container (pyosmium), invoked by app/setup/places.py:
    python /setup/places_build.py OUT.sqlite CC=/data/app/places-us.osm.pbf [CC=...]

The schema/insert helpers have no osmium dependency so the host can import
them for tests.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

KINDS = {"city": 0, "town": 1, "village": 2}


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
        CREATE TABLE places (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT, country TEXT,
            kind TEXT, population INTEGER, lon REAL, lat REAL);
        CREATE VIRTUAL TABLE places_fts USING fts5(name, content='places', content_rowid='id',
            tokenize='unicode61 remove_diacritics 2');
    """)


def insert_rows(con: sqlite3.Connection, rows) -> None:
    con.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO places_fts(places_fts) VALUES ('rebuild')")


def population(tags) -> int:
    raw = tags.get("population", "")
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else 0


def iter_places(pbf: str, country: str):
    import osmium  # type: ignore  (container only)

    class H(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.rows = []

        def node(self, n):
            kind = n.tags.get("place")
            if kind not in KINDS:
                return
            name = n.tags.get("name:en") or n.tags.get("name")
            if not name:
                return
            region = n.tags.get("addr:state") or n.tags.get("is_in:state") or n.tags.get("is_in") or ""
            self.rows.append((n.id, name, region.split(",")[0].strip()[:40], country, kind,
                              population(n.tags), round(n.location.lon, 5), round(n.location.lat, 5)))

    h = H()
    h.apply_file(pbf)
    return h.rows


def main(argv: list[str]) -> int:
    out = Path(argv[0])
    rows = []
    for arg in argv[1:]:
        cc, pbf = arg.split("=", 1)
        r = iter_places(pbf, cc.upper())
        print(f"{cc}: {len(r):,} places", flush=True)
        rows += r
    tmp = out.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    create_schema(con)
    insert_rows(con, rows)
    con.commit()
    con.close()
    os.replace(tmp, out)
    print(f"wrote {out} ({len(rows):,} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
