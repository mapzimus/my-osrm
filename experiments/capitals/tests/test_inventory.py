import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inventory as I  # noqa: E402


def test_divisions():
    by = {c: [d for d in I.DIVISIONS if d[0] == c] for c in ("US", "CA", "MX")}
    assert (len(by["US"]), len(by["CA"]), len(by["MX"])) == (50, 13, 32)
    assert len({d[1] for d in I.DIVISIONS}) == len(I.DIVISIONS)
    assert not any("Puerto Rico" in d[1] or "District of Columbia" in d[1] for d in I.DIVISIONS)


def test_pick_takes_the_candidate_near_the_capital_city():
    city = (-97.7431, 30.2672)                              # Austin
    cands = [("Q1", (-87.0, 30.0)), ("Q2", (-97.7404, 30.2747)), ("Q3", (-97.75, 30.3))]
    assert I.pick(cands, city) == ("Q2", (-97.7404, 30.2747))


def test_pick_refuses_far_candidates():
    assert I.pick([("Q1", (-87.0, 30.0))], (-97.7431, 30.2672)) is None
