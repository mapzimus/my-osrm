"""reel_tour: projection, route runs, metro runs, state codes, part arithmetic."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reel_tour as T  # noqa: E402


def test_projection_origin_and_scale():
    x, y = T.km(T.LON0, T.PHI0)
    assert abs(x) < 1e-9 and abs(y) < 1e-9
    (x0, x1), (y0, y1) = T.km([-71.06, -118.24], [42.36, 34.05])        # Boston, Los Angeles
    assert 3900 < np.hypot(x1 - x0, y1 - y0) < 4300                     # ~4,170 km great circle


def test_state_codes_both_ways():
    assert T.code("WI") == "WI" and T.code("Wisconsin") == "WI" and T.code("Ontario") == "ON"
    assert T.CENSUS_REGION["TX"] == "South" and T.CENSUS_REGION["HI"] == "West"
    assert len({c for c in T.STATE_NAME if len(c) == 2 and c not in T.CENSUS_REGION and c != "PR"}) == 13, \
        "the 13 provinces and territories have no census region"


def test_split_runs_follows_mode_changes():
    mode = np.array(["road", "road", "road", "ferry", "ferry", "road", "road"])
    assert T.split_runs(mode) == [("road", 0, 3), ("ferry", 2, 5), ("road", 4, 7)]


def test_metro_runs_picks_the_longest_consecutive_run():
    st = [dict(lon=-84.4 + 0.01 * j, lat=33.75) for j in range(5)]           # 5 in Atlanta
    st += [dict(lon=-90.0, lat=35.0)]                                       # one far away
    st += [dict(lon=-84.4, lat=33.76 + 0.01 * j) for j in range(2)]         # 2 more in Atlanta
    assert T.metro_runs(st, {"Atlanta": (-84.4, 33.75, 50)}) == [("Atlanta", 0, 4)]
    assert T.near(st, -90.0, 35.0, 10).tolist() == [5]


def test_ferry_name_tidy():
    assert T.ferry_name("Linconlville - Islesboro") == "Lincolnville – Islesboro"


def test_part_frames_tile_the_video():
    n = 65 * 30
    parts = [T.part_frames(n, f"{k}/3") for k in (1, 2, 3)]
    assert parts[0].start == 0 and parts[-1].stop == n
    assert all(a.stop == b.start for a, b in zip(parts, parts[1:]))
    assert T.part_frames(n, None) == range(n)
    with pytest.raises(ValueError):
        T.part_frames(n, "4/3")
