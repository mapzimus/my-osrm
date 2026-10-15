"""Every major airport in the lower 48 (idea #79: 1 hour from every major airport).

"Major" = the FAA's large and medium hubs (CY2025 commercial service
enplanements, cache/faa_cy2025.xlsx): the airports with nonstops to most of the
country. Coordinates from OurAirports (cache/ourairports.csv), matched on the
FAA location id (IATA code, or local code when they differ).

    python experiments/airports/inventory.py

Writes cache/stores.csv (chain, name, lon, lat, state, code, hub, enplanements)
for experiments/dunkin-vs-starbucks/drivetime.py --stores; one "chain": airport.
"""

from __future__ import annotations

import csv
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
NOT_L48 = {"AK", "HI", "PR", "GU", "VI", "AS", "MP"}
HUBS = {"L": "large", "M": "medium"}


def main() -> None:
    ws = openpyxl.load_workbook(CACHE / "faa_cy2025.xlsx", read_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    head = rows[0]
    faa = [dict(zip(head, r)) for r in rows[1:] if r[0] is not None]
    faa = [r for r in faa if r["Hub"] in HUBS and r["ST"] not in NOT_L48]

    by_code = {}
    with open(CACHE / "ourairports.csv", encoding="utf-8") as fh:
        for a in csv.DictReader(fh):
            if a["iso_country"] != "US":
                continue
            for k in (a["iata_code"], a["local_code"]):
                if k and (k not in by_code or a["type"] == "large_airport"):
                    by_code[k] = a
    out, missing = [], []
    for r in faa:
        a = by_code.get(r["Locid"])
        if a is None:
            missing.append(r["Locid"])
            continue
        out.append(dict(chain="airport", name=r["Airport Name"], lon=float(a["longitude_deg"]),
                        lat=float(a["latitude_deg"]), state=r["ST"], code=r["Locid"], hub=HUBS[r["Hub"]],
                        enplanements=int(r["CY 25 Enplanements"])))
    with open(CACHE / "stores.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, list(out[0]))
        w.writeheader()
        w.writerows(out)
    n = {h: sum(1 for o in out if o["hub"] == h) for h in HUBS.values()}
    print(f"{len(out)} major airports in the lower 48 ({n}); not matched: {missing}")


if __name__ == "__main__":
    main()
