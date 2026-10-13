"""Shots and ferry timing for camera_video.py (no OSRM needed)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import animate as A  # noqa: E402
import ferries_anim as FA  # noqa: E402
from shots import Shots  # noqa: E402

FULL = (0.0, 0.0, 1.5)


def test_clock_monotonic_and_ends_at_t_end():
    sh = Shots(A.Albers(), 138.0, FULL)
    ts = [sh.hours(s) for s in np.arange(0, sh.duration + 0.01, 1 / 30)]
    assert all(b >= a - 1e-9 for a, b in zip(ts, ts[1:]))
    assert ts[0] == 0.0 and abs(ts[-1] - 138.0) < 1e-6


def test_camera_continuous():
    sh = Shots(A.Albers(), 138.0, FULL)
    vs = np.array([sh.view(s) for s in np.arange(0, sh.duration, 1 / 30)])
    step = np.abs(np.diff(vs, axis=0))
    # no frame-to-frame jump bigger than 4% of the view width
    assert (step[:, :2].max(axis=1) < 0.04 * vs[1:, 2]).all()
    assert (np.abs(np.diff(np.log(vs[:, 2]))) < 0.05).all()


def test_hawaii_only_where_scripted():
    sh = Shots(A.Albers(), 138.0, FULL)
    assert sh.hawaii(5) == 0 and sh.hawaii(13) == 1 and sh.hawaii(25) == 0 and sh.hawaii(44) == 1


def test_parse_duration():
    assert FA.parse_duration("09:00") == 9
    assert abs(FA.parse_duration("00:01:30") - 0.025) < 1e-9
    assert FA.parse_duration("45") == 0.75
    assert FA.parse_duration("") is None


def test_used_rule():
    # far end lights a full crossing later: the boat got there
    assert FA.classify(10.0, 12.5, 2.0) == (10.5, 12.5, False)
    # reversed ends
    assert FA.classify(12.5, 10.0, 2.0) == (10.5, 12.5, True)
    # far end lit almost at once: a road got there first
    assert FA.classify(10.0, 10.3, 2.0) is None
    assert FA.classify(None, 3.0, 1.0) is None


def test_merge_chains_joins_two_leg_stop_only():
    a = ("FP-Akutan", [[0, 0], [1, 0]], 10.25)
    b = ("Akutan-Unalaska", [[2, 0], [1, 0]], 3.5)     # reversed, shares (1,0)
    c = ("Hub-X", [[5, 5], [6, 6]], 1.0)
    merged = FA.merge_chains([a, b, c])
    names = sorted(n for n, _, _ in merged)
    assert names == ["FP-Akutan / Akutan-Unalaska", "Hub-X"]
    m = next(x for x in merged if "/" in x[0])
    assert m[1] == [[0, 0], [1, 0], [2, 0]] and m[2] == 13.75
    # three legs at one port: no merge there
    d = ("Other", [[1, 0], [1, 1]], 2.0)
    assert len(FA.merge_chains([a, b, d])) == 3
