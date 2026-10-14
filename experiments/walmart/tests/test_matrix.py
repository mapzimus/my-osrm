# experiments/walmart/tests/test_matrix.py
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matrix as M


def fake_polyline(pts):
    return ",".join(f"{x:g}_{y:g}" for x, y in pts)


def fake_get_factory(calls):
    """Duration = |x_i - x_j| * 10 s; point with x == 99 is unreachable."""
    def get(url):
        calls.append(url)
        u = urlparse(url)
        pts = [tuple(map(float, p.split("_"))) for p in u.path.split("polyline(")[1].rstrip(")").split(",")]
        q = parse_qs(u.query)
        src = [int(v) for v in q["sources"][0].split(";")]
        dst = [int(v) for v in q["destinations"][0].split(";")]
        dur = [[None if 99 in (pts[i][0], pts[j][0]) and i != j else abs(pts[i][0] - pts[j][0]) * 10
                for j in dst] for i in src]
        return dict(code="Ok", durations=dur, distances=[[None if v is None else v * 30 for v in r] for r in dur],
                    sources=[dict(location=[pts[i][0], pts[i][1] + 0.001]) for i in src])
    return get


def test_blocks():
    assert M.blocks(5, 2) == [(0, 2), (2, 4), (4, 5)]


def test_table_matches_brute_force(tmp_path):
    pts = [[float(x), 0.0] for x in (0, 3, 7, 99, 12)]
    calls = []
    dur, dist, road = M.table(pts, fake_get_factory(calls), fake_polyline, "http://x", tmp_path, size=2)
    want = np.array([[abs(a[0] - b[0]) * 10 for b in pts] for a in pts], np.float32)
    ok = ~np.isnan(dur)
    assert dur.dtype == np.float32 and dur.shape == (5, 5)
    assert np.isnan(dur[3, 0]) and np.isnan(dur[0, 3]) and dur[3, 3] == 0
    assert np.allclose(dur[ok], want[ok]) and np.allclose(dist[ok], want[ok] * 30)
    assert road[1] == [3.0, 0.001]
    assert len(calls) == 9                      # 3 x 3 blocks


def test_resume_skips_done_blocks(tmp_path):
    pts = [[float(x), 0.0] for x in (0, 3, 7, 12)]
    M.table(pts, fake_get_factory([]), fake_polyline, "http://x", tmp_path, size=2)
    calls = []
    M.table(pts, fake_get_factory(calls), fake_polyline, "http://x", tmp_path, size=2)
    assert calls == []
