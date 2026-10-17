"""Drop ferries that don't carry cars from the routing network.

OSM tags many passenger boats foot=yes and says nothing about cars; stock
OSRM routes cars onto anything route=ferry that isn't motor_vehicle=no. That
let cars ride the CG Railway rail ferry (Mobile AL -> Coatzacoalcos), the
Victoria Clipper (Seattle -> Victoria, foot passengers only), barge lines
and tour boats.

Rule (is_car_ferry): keep a ferry if any tag says cars are allowed
(motor_vehicle / motorcar / vehicle = yes|designated|permissive, or cargo
lists vehicles). Otherwise drop it if it's for rail or freight, or if it is
explicitly for foot passengers (foot / bicycle = yes|designated) while
silent on cars. Ferries tagged nothing at all stay (OSRM's default).

The name lists cover North America and Europe. A passenger-boat name
(PASSENGER_RE) outranks the operator keep-list, because car-ferry companies
also run passenger-only fast boats (Norled's hurtigbåt, Baleària Caribbean).

Runs in the my-osrm-tools container:

    python car_ferries.py <in.osm.pbf> <out.osm.pbf> <work_dir>
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import osmium

YES = {"yes", "designated", "permissive"}
# Whole-word name/operator patterns for boats that never take a private car:
# rail and freight lines, plus sightseeing ("Canada-USA Border Cruise" on
# Waterton Lake is tagged nothing at all). Word boundaries matter: a plain
# "rail " substring also matched "West Coast Trail".
DROP_RE = re.compile(
    r"\b(railway|rail|barge|freight|cargo|marine lines|cruise|tour|clipper|water taxi|"
    r"passenger)\b")
# Passenger-only boats run by companies that are otherwise car ferries, so
# this is checked BEFORE the keep-list: Norled / Torghatten Nord "hurtigbåt"
# (fast passenger boat, 35 operator values in OSM), Greek hydrofoils, and
# Baleària's Fort Lauderdale-Bahamas run (no cars; Baleària in Spain takes them).
PASSENGER_RE = re.compile(
    r"\b(bale[aà]ria caribbean|hurtigb[åa]t|passasjer|hydrofoil|flying dolphin|"
    r"passenger ferry|foot ferry|water taxi)\b")
# Operators / routes that are known car ferries but whose OSM tags only say
# foot=yes (the rule below would drop them). Spelled as the operator values
# appear in OSM (taginfo, Oct 2026); whole words, so "MobyKlick" (bike share)
# and "Hanekamp" don't match moby / anek.
KEEP_RE = re.compile(
    # North America
    r"\b(washington state ferries|wsf|steamship authority|chi-cheemaun|owen sound|"
    r"lake express|lake michigan carferry|badger|bc ferries|alaska marine highway|"
    r"marine atlantic|baja ferries|oxford-bellevue|green river ferry|valley view ferry|"
    r"kennebecasis|ultramar|transbordador|naviera|tobermory|south baymouth|"
    r"peace river barge|fox lake access|"
    # North Sea, Channel, Irish Sea
    r"stena line|dfds|p&o ferries|brittany ferries|irish ferries|condor ferries|"
    r"isle of man steam packet|wightlink|red funnel|caledonian macbrayne|calmac|"
    r"northlink|pentland ferries|orkney ferries|shetland islands council|western ferries|"
    r"rederij doeksen|rederij wagenborg|wyker dampfschiffs-reederei|"
    # Baltic, Nordic (Norwegian fjord ferries: norled, fjord1, torghatten, boreal)
    r"color line|fjord line|tallink|silja line|viking line|finnlines|eckerö lin\w*|"
    r"wasaline|scandlines|tt-line|polferries|unity line|molslinjen|bornholmslinjen|"
    r"forsea|destination gotland|smyril line|norled|fjord1|torghatten|boreal sjø|"
    r"bastø fosen|fosennamsos|färjerederiet|ventrafiken|samsø rederi|færgen|"
    # Mediterranean, Adriatic, Aegean
    r"baleària|balearia|trasmed\w*|transmediterr\w*|naviera armas|fred\. olsen|"
    r"corsica ferries|moby|tirrenia|toremar|siremar|caronte|gnv|grandi navi veloci|"
    r"grimaldi lines|jadrolinija|minoan lines|anek|superfast ferries|blue star|"
    r"hellenic seaways|levante ferries|ventouris ferries|seajets|"
    # Lake Geneva car ferries
    r"compagnie générale de navigation|cgn)\b")
NO_CARS_RE = re.compile(r"\b(no cars|no vehicles|passengers? only|foot passengers? only)\b")


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def is_car_ferry(tags) -> tuple[bool, str]:
    t = {k: v for k, v in tags}
    cargo = t.get("cargo", "")
    if any(t.get(k) in YES for k in ("motor_vehicle", "motorcar", "vehicle")) or "vehicle" in cargo:
        return True, "cars allowed"
    note = f"{t.get('note', '')} {t.get('description', '')}".lower()
    if NO_CARS_RE.search(note):
        return False, "note says no cars"
    if "railway" in t or "rail" in cargo or cargo in {"goods", "freight", "container"}:
        return False, "rail/freight tags"
    label = f"{t.get('name', '')} {t.get('operator', '')}".lower()
    if PASSENGER_RE.search(label):
        return False, "passenger-boat name"
    if KEEP_RE.search(label):
        return True, "known car-ferry operator"
    if DROP_RE.search(label):
        return False, "rail/freight/tour/passenger name"
    if t.get("foot") in YES or t.get("bicycle") in YES:
        return False, "foot passengers, silent on cars"
    return True, "untagged (OSRM default)"


def main(src: str, dst: str, work: str) -> None:
    wd = Path(work)
    wd.mkdir(parents=True, exist_ok=True)
    ferries = wd / "all-ferries.osm.pbf"
    run("osmium", "tags-filter", "--overwrite", "-o", str(ferries), src, "w/route=ferry")

    drop, report = [], []
    for w in osmium.FileProcessor(str(ferries), osmium.osm.WAY):
        keep, why = is_car_ferry(w.tags)
        report.append({"way": w.id, "name": w.tags.get("name", ""), "keep": keep, "why": why})
        if not keep:
            drop.append(w.id)
    (wd / "ferry-filter-report.json").write_text(json.dumps(report, indent=1))
    kept = sum(r["keep"] for r in report)
    print(f"ferries: {len(report)} total, {kept} kept, {len(drop)} dropped", flush=True)

    ids = wd / "dropped-ferries.txt"
    ids.write_text("".join(f"w{i}\n" for i in drop))
    if drop:
        run("osmium", "removeid", "--overwrite", "-i", str(ids), "-o", dst, src)
    else:
        run("osmium", "cat", "--overwrite", "-o", dst, src)


if __name__ == "__main__":
    main(*sys.argv[1:])
