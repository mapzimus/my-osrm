"""Every national park in the 50 states (viewer request, 2026-10-16), one trip (Optimal Drives).

Parks: NPS Land Resources Division boundaries, UNIT_TYPE 'National Parks' (62), plus New River
Gorge (filed as 'National Preserves'), minus American Samoa and the Virgin Islands -> 61.
Stop: one visitor centre per park from the NPS API (developer.nps.gov, DEMO_KEY), the park's
main one (PICK). Max (2026-10-16): all 61, flying to the ones with no road.

Access (data probe, 2026-10-16):
- road: drive to the visitor centre (Glacier Bay through the Alaska ferry to Gustavus).
- boat: drive to the dock, then a passenger boat out and back (access.py adds it):
  Isle Royale (Grand Portage -> Windigo), Dry Tortugas (Key West -> Garden Key),
  Channel Islands (Ventura -> Scorpion, Santa Cruz Island).
- bush: Gates of the Arctic, Kobuk Valley, Lake Clark, Katmai have no road. The drive stops at
  Anchorage airport; access.py adds one bush-plane loop to all four and back.
- Hawaii: walmart/build.py flies there (flights.py) and drives on Maui and the Big Island.

    python experiments/national_parks/inventory.py     # -> cache/stores.json for walmart/build.py --cache

Inputs: cache/nps_parks.geojson (boundary layer 2, simplified) and cache/nps_vc.json
(/visitorcenters?limit=1000). The NPS API's park code for Sequoia and Kings Canyon is 'seki'.
"""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import Point, shape

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"

# park -> visitor centre name in the NPS API (api code, name)
PICK = {
    "ACAD": ("acad", "Hulls Cove Visitor Center"), "ARCH": ("arch", "Arches Visitor Center"),
    "BADL": ("badl", "Ben Reifel Visitor Center"), "BIBE": ("bibe", "Panther Junction Visitor Center"),
    "BISC": ("bisc", "Dante Fascell Visitor Center"), "BLCA": ("blca", "South Rim Visitor Center"),
    "BRCA": ("brca", "Bryce Canyon Visitor Center"), "CANY": ("cany", "Island in the Sky Visitor Center"),
    "CARE": ("care", "Capitol Reef National Park Visitor Center"), "CAVE": ("cave", "Visitor Center"),
    "CHIS": ("chis", "Scorpion Ranch Visitor Center on Santa Cruz Island"),
    "CONG": ("cong", "Harry Hampton Visitor Center"), "CRLA": ("crla", "Steel Information Center"),
    "CUVA": ("cuva", "Boston Mill Visitor Center"), "DENA": ("dena", "Denali Visitor Center"),
    "DEVA": ("deva", "Furnace Creek Visitor Center"), "DRTO": ("drto", "Garden Key Visitor Center"),
    "EVER": ("ever", "Ernest F. Coe Visitor Center"),
    "GAAR": ("gaar", "Anaktuvuk Pass Ranger Station"),     # village inside the park, scheduled flights
    "GLAC": ("glac", "Apgar Visitor Center"), "GLBA": ("glba", "Glacier Bay National Park Visitor Center"),
    "GRBA": ("grba", "Lehman Caves Visitor Center"), "GRCA": ("grca", "Grand Canyon Visitor Center (South Rim)"),
    "GRSA": ("grsa", "Great Sand Dunes Visitor Center"), "GRSM": ("grsm", "Sugarlands Visitor Center"),
    "GRTE": ("grte", "Craig Thomas Discovery & Visitor Center"), "GUMO": ("gumo", "Pine Springs Visitor Center"),
    "HALE": ("hale", "Headquarters Visitor Center"), "HAVO": ("havo", "Welcome Center"),
    "HOSP": ("hosp", "Fordyce Bathhouse Visitor Center and Museum"),
    "INDU": ("indu", "Dorothy Buell Memorial Visitor Center (Indiana Dunes Visitor Center)"),
    "ISRO": ("isro", "Windigo Visitor Center"), "JEFF": ("jeff", "Gateway Arch Visitor Center"),
    "JOTR": ("jotr", "Joshua Tree Visitor Center"), "KATM": ("katm", "Brooks Camp Visitor Center"),
    "KEFJ": ("kefj", "Exit Glacier Nature Center"), "KICA": ("seki", "Kings Canyon Visitor Center"),
    "LACL": ("lacl", "Port Alsworth Visitor Center"), "LAVO": ("lavo", "Kohm Yah-mah-nee Visitor Center"),
    "MACA": ("maca", "Mammoth Cave Visitor Center"), "MEVE": ("meve", "Mesa Verde Visitor and Research Center"),
    "MORA": ("mora", "Henry M Jackson Memorial Visitor Center"), "NERI": ("neri", "Canyon Rim Visitor Center"),
    "NOCA": ("noca", "North Cascades Visitor Center"), "OLYM": ("olym", "Olympic National Park Visitor Center"),
    "PEFO": ("pefo", "Painted Desert Visitor Center"), "PINN": ("pinn", "Bear Gulch Nature Center"),
    "REDW": ("redw", "Thomas H. Kuchel Visitor Center"), "ROMO": ("romo", "Beaver Meadows Visitor Center"),
    "SAGU": ("sagu", "Red Hills Visitor Center (West)"), "SEQU": ("seki", "Foothills Visitor Center"),
    "SHEN": ("shen", "Harry F. Byrd Sr. Visitor Center"), "THRO": ("thro", "South Unit Visitor Center"),
    "VOYA": ("voya", "Rainy Lake Visitor Center"), "WHSA": ("whsa", "White Sands Visitor Center"),
    "WICA": ("wica", "Wind Cave Visitor Center"), "WRST": ("wrst", "Kennecott Visitor Center"),
    "YELL": ("yell", "Old Faithful Visitor Education Center"), "YOSE": ("yose", "Yosemite Valley Welcome Center"),
    "ZION": ("zion", "Zion Canyon Visitor Center"),
}
# Kobuk Valley's only listed centre is in Kotzebue, 100 km outside; fly-in visitors land at the
# Great Kobuk Sand Dunes.
KOBUK_DUNES = (-159.05, 67.08)

# boat parks: (dock you drive to, boat miles, boat hours) each way
BOAT = {
    "ISRO": ((-89.6845, 47.9614), "Grand Portage", 22, 1.5),
    "DRTO": ((-81.8030, 24.5605), "Key West", 70, 2.25),
    "CHIS": ((-119.2666, 34.2485), "Ventura", 25, 1.0),
}
BUSH = ("LACL", "KATM", "KOVA", "GAAR")
ANC = (-149.9961, 61.1744)                       # Ted Stevens Anchorage airport

SKIP = {"NPSA", "VIIS"}                           # territories, not states
STATE = {"CA-NV": "CA", "FL 6/": "FL", "ID-MT-WY": "WY", "MO-IL": "MO", "TN-NC": "TN"}   # where the stop is


def main() -> None:
    parks = json.load(open(CACHE / "nps_parks.geojson", encoding="utf-8"))["features"]
    vcs = json.load(open(CACHE / "nps_vc.json", encoding="utf-8"))["data"]
    vc = {(v["parkCode"], v["name"]): (float(v["longitude"]), float(v["latitude"])) for v in vcs
          if v["latitude"]}
    rows = [(f["properties"]["UNIT_CODE"], f["properties"]["UNIT_NAME"], f["properties"]["STATE"],
             shape(f["geometry"])) for f in parks]
    rows.append(("NERI", "New River Gorge National Park", "WV", None))

    stores = []
    for code, name, st, geom in rows:
        if code in SKIP:
            continue
        park = (vc[PICK[code]] if code != "KOVA" else KOBUK_DUNES)
        if geom is not None:
            off = geom.distance(Point(park)) * 111
            if off > 2:
                print(f"  note: {code} stop is {off:.0f} km outside the boundary ({PICK[code][1]})")
        s = dict(name=name.replace(" National Park", "").replace("Hawai'i", "Hawaiʻi").replace("Haleakala", "Haleakalā"),
                 code=code, state=STATE.get(st, st), park=list(park), access="road")
        if code in BOAT:
            dock, port, mi, h = BOAT[code]
            s.update(access="boat", lon=dock[0], lat=dock[1], town=port, boat_mi=mi, boat_h=h)
        elif code in BUSH:
            s.update(access="bush", lon=ANC[0], lat=ANC[1], town="Anchorage")
        else:
            s.update(lon=park[0], lat=park[1])
        stores.append(s)

    stores.sort(key=lambda s: s["code"])
    assert len(stores) == 61 and len({s["code"] for s in stores}) == 61
    json.dump(stores, open(CACHE / "stores.json", "w", encoding="utf-8"), indent=0, ensure_ascii=False)
    by = {a: sum(s["access"] == a for s in stores) for a in ("road", "boat", "bush")}
    print(f"{len(stores)} parks in {len({s['state'] for s in stores})} states: {by}")


if __name__ == "__main__":
    main()
