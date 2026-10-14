# experiments/walmart/tests/test_build.py
import sys
from pathlib import Path

import numpy as np
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build as B
import flights as F


def test_bad_rows():
    d = np.zeros((6, 6), np.float32)
    d[2, :] = np.nan                 # can't leave store 2
    d[:, 4] = np.nan                 # can't reach store 4
    d[2, 2] = d[4, 4] = 0
    assert B.bad_rows(d) == [2, 4]


def test_chunks_overlap():
    assert B.chunks(5, 400) == [(0, 4)]
    c = B.chunks(1000, 400)
    assert c[0] == (0, 399) and all(b[0] == a[1] for a, b in zip(c, c[1:])) and c[-1][1] == 999
    assert all(b - a + 1 <= 400 for a, b in c)


def test_options():
    assert ("LAX", "HNL") in F.options("US", "HI") and ("HNL", "LAX") in F.options("HI", "US")
    assert all(a != b for a, b in F.options("HI", "HI")) and ("HNL", "OGG") in F.options("HI", "HI")
    assert F.options("PR", "HI") == [("SJU", b) for b in F.ISLAND["HI"]]


def test_best_hop_picks_cheapest_pair():
    codes = list(F.AIRPORTS)
    nA = len(codes)
    to = np.full((2, nA), 1e6, np.float32)
    frm = np.full((nA, 2), 1e6, np.float32)
    to[0, codes.index("LAX")] = 3600                     # store 0 (US) is an hour from LAX
    frm[codes.index("HNL"), 1] = 1800                    # store 1 (HI) is 30 min from HNL
    c, (a, b) = F.best_hop(0, 1, "US", "HI", to, frm, codes)
    assert (a, b) == ("LAX", "HNL")
    assert c == pytest.approx(3600 + F.flight_s(F.AIRPORTS["LAX"], F.AIRPORTS["HNL"]) + 1800)


def test_check_tour_catches_repeats():
    tour = dict(stops=[dict(name="a", t=0), dict(name="a", t=5)], t=[0, 5], legs=[dict(h=0.001, ferry=False)],
                flights=[], hours=5 / 3600)
    with pytest.raises(AssertionError):
        B.check_tour(tour, 2)


def test_check_tour_long_legs():
    base = dict(stops=[dict(name="a", t=0), dict(name="b", t=5)], t=[0, 5], flights=[], hours=5 / 3600)
    with pytest.raises(AssertionError):
        B.check_tour({**base, "legs": [dict(h=13, ferry=False, alaska=False)]}, 2)
    B.check_tour({**base, "legs": [dict(h=40, ferry=False, alaska=True)]}, 2)       # Alaska: allowed
