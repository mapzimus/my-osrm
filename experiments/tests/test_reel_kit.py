"""The kit's pure functions: pacing, clock holds, shot layout, the camera path, platform layouts.

    python -m pytest experiments/tests
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reel_kit as K  # noqa: E402


@pytest.fixture(autouse=True)
def classic_layout():
    yield
    K.use("classic")


def test_clock_and_smoothstep():
    assert K.clock(0) == "0:00"
    assert K.clock(1.5) == "1:30"
    assert K.clock(983.99) == "983:59"
    assert K.smoothstep(-1) == 0 and K.smoothstep(2) == 1
    assert K.smoothstep(0.5) == pytest.approx(0.5)


def test_use_sets_every_platform_band():
    for p, spec in K.PLATFORMS.items():
        K.use(p)
        assert K.TOP == spec["top"] and K.TEXT_FLOOR == spec["floor"] and K.RIGHT_RAIL == spec["rail"]
        assert K.MAP_TOP == K.TOP + K.HUD_H
        assert K.NOW_TOP < K.NOW_BOTTOM <= K.TEXT_FLOOR
        assert K.MAP_TOP < K.MAP_BOTTOM == K.NOW_TOP


def test_warp_is_monotonic_and_spans_the_drive():
    stops = np.array([0.0, 1.0, 1.1, 1.2, 1.3, 9.0, 10.0])     # a cluster then an empty road
    d = K.warp(stops, 10.0, 300)
    assert len(d) == 300 and d[0] == 0 and d[-1] == pytest.approx(10.0)
    assert np.all(np.diff(d) >= 0)
    # Half the pace follows the stops: the cluster (1.0 to 1.3 h) gets far more than 3 % of the frames.
    assert ((d >= 1.0) & (d <= 1.3)).sum() > 0.2 * 300


def test_hold_clock_inserts_still_frames():
    core = np.linspace(0, 10, 101)
    out = K.hold_clock(core, [(2.0, 5), (7.0, 3)])
    assert len(out) == 101 + 8
    assert np.all(np.diff(out) >= 0)
    assert (out == 2.0).sum() == 6 and (out == 7.0).sum() == 4     # the original frame plus the hold


def test_lay_shots_covers_the_drive_without_gaps():
    f0, f1 = 120, 1800
    cam = lambda i, e: (float(i), float(e), 700.0)              # noqa: E731
    special = [(600, 660, (0.0, 0.0, 90.0)), (900, 1000, (1.0, 1.0, 650.0))]
    shots = K.lay_shots(special, f0, f1, cam)
    assert shots[0][0] == f0 and shots[-1][1] == f1
    for (a0, b0, _), (a1, b1, _) in zip(shots, shots[1:]):
        assert b0 == a1, "shots are back to back"
    assert (600, 660, (0.0, 0.0, 90.0)) in shots and (900, 1000, (1.0, 1.0, 650.0)) in shots
    road = [s for s in shots if s[2][2] == 700.0]
    assert all(b - a >= 0.4 * K.FPS for a, b, _ in road)        # no blink-length road shots


def test_lay_shots_drops_a_special_that_is_already_past():
    shots = K.lay_shots([(10, 20, (0, 0, 1))], 100, 400, lambda i, e: (0, 0, 2))
    assert shots[0][0] == 100 and shots[-1][1] == 400
    assert all(cam == (0, 0, 2) for _, _, cam in shots), "only road shots: the special ended before f0"


def test_camera_path_keeps_the_car_in_frame_and_caps_zoom_speed():
    pytest.importorskip("scipy")
    n, f0, f1 = 600, 60, 540
    shots = [(f0, 300, (0.0, 0.0, 800.0)), (300, f1, (0.0, 0.0, 800.0))]
    car_x = np.linspace(0, 900, n)                               # drifts out of a 800 km frame held on 0
    car_y = np.zeros(n)
    sx, sy, slw = K.camera_path(shots, n, f0, f1, car_x, car_y)
    w = np.exp(slw[f0:f1 + 1])
    half = w / 2 * (K.W / 2 - K.CAR_MARGIN) / (K.W / 2)
    # The widening is rate-capped and smoothed, so a car drifting this fast (56 km/s) can poke
    # ~15 % past the margin before the frame catches up; it never gets near the edge (ratio 1.35).
    assert np.max(np.abs(car_x[f0:f1 + 1] - sx[f0:f1 + 1]) / half) < 1.2, "the car never leaves the frame"
    assert np.all(w >= 800 - 1e-6), "the frame only widens for the car"
    assert w[-1] > 1500, "it did widen for the car at 810 km"
    assert np.max(np.abs(np.diff(slw[f0:f1 + 1]))) <= K.ZOOM_RATE_MAX / K.FPS + 1e-9


def test_fit_cam_width_and_centre():
    x, y, w = K.fit_cam([0, 100], [0, 10], min_w=50)
    assert w == pytest.approx(130)                              # 100 km + 15 % each side
    assert x == pytest.approx(50)
    x, y, w = K.fit_cam([0, 1], [0, 1], min_w=650)
    assert w == 650 and abs(y - 0.5) > 0, "centred between the HUD and the now band, not the frame"


def test_part_of_the_shade_is_solid_above_the_hud():
    K.use("tiktok")
    sh = np.asarray(K.shade())
    assert sh[0, 0, 3] == 235 and sh[K.MAP_CENTER_Y.__int__(), 0, 3] == 0


def test_arc_starts_and_ends_at_the_airports():
    at = K.arc((0, 0), (100, 0))
    assert at(0)[:2] == pytest.approx((0, 0)) and at(1)[:2] == pytest.approx((100, 0))
    assert at(0.5)[1] < 0, "bowed upward (screen y grows downward)"
    assert math.isclose(at(0.5)[2], 0, abs_tol=1e-9), "level at the top of the arc"
