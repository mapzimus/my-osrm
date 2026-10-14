import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inventory as I


def test_keeps_store_types():
    assert I.is_store({"shop": "department_store", "name": "Walmart Supercenter"})
    assert I.is_store({"shop": "supermarket", "name": "Walmart Supercenter"})
    assert I.is_store({"shop": "department_store", "name": "Walmart"})


def test_drops_neighborhood_markets():
    # Max, 2026-10-14: big stores only; OSM has 475 of 674 markets, too few to count.
    assert not I.is_store({"shop": "supermarket", "name": "Walmart Neighborhood Market"})


def test_drops_non_stores():
    assert not I.is_store({"amenity": "fuel", "name": "Walmart Fuel Station"})
    assert not I.is_store({"shop": "department_store", "amenity": "pharmacy", "name": "Walmart Pharmacy"})
    assert not I.is_store({"shop": "optician", "name": "Walmart Vision Center"})
    assert not I.is_store({"shop": "car_repair", "name": "Walmart Auto Care Center"})
    assert not I.is_store({"shop": "wholesale", "name": "Sam's Club"})
    assert not I.is_store({"shop": "department_store", "name": "Walmart", "opening_hours": "closed"})
    assert not I.is_store({"amenity": "parcel_locker", "name": "Walmart Pickup"})


def test_kind():
    assert I.kind("Walmart Supercenter") == "supercenter"
    assert I.kind("Walmart Neighborhood Market") == "market"
    assert I.kind("Walmart") == "store"


def test_dedupe():
    a = dict(name="Walmart Supercenter", lon=-84.0, lat=33.0, osm="w1", area=1)
    b = dict(name="Walmart Supercenter", lon=-84.0005, lat=33.0003, osm="n2", area=0)   # ~60 m away
    c = dict(name="Walmart Supercenter", lon=-84.05, lat=33.0, osm="w3", area=1)       # ~4.7 km away
    out = I.dedupe([b, a, c])
    assert sorted(s["osm"] for s in out) == ["w1", "w3"]      # the polygon wins
