# experiments/walmart/tests/test_lkh.py
import itertools
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lkh as L


def path_cost(cost, order):
    return sum(cost[a, b] for a, b in zip(order, order[1:]))


def test_read_order_drops_dummy_and_rotates(tmp_path):
    p = tmp_path / "t.tour"
    p.write_text("NAME : t\nTYPE : TOUR\nDIMENSION : 4\nTOUR_SECTION\n3\n1\n4\n2\n-1\nEOF\n")
    assert L.read_order(p) == [2, 0, 1]          # nodes after dummy 1: 4,2 then 3 -> stops 2,0,1


def test_costs_fit():
    with pytest.raises(ValueError):
        L.to_int(np.array([[0, L.LIMIT]], np.float64))
    assert L.to_int(np.array([[0.4, 12.6]])).tolist() == [[0, 13]]


docker = subprocess.run(["docker", "image", "inspect", L.IMAGE], capture_output=True,
                        stdin=subprocess.DEVNULL).returncode == 0


@pytest.mark.skipif(not docker, reason="walmart-lkh image not built")
def test_solve_open_is_optimal_on_small_asymmetric(tmp_path):
    rng = np.random.default_rng(1)
    cost = rng.integers(10, 1000, (8, 8)).astype(np.float64)
    order = L.solve_open(cost, [], tmp_path, 10)
    best = min(path_cost(cost, p) for p in itertools.permutations(range(8)))
    assert sorted(order) == list(range(8)) and path_cost(cost, order) == best


@pytest.mark.skipif(not docker, reason="walmart-lkh image not built")
def test_no_ends_never_start_or_finish(tmp_path):
    cost = np.array([[0, 1, 50, 50], [1, 0, 1, 50], [50, 1, 0, 1], [50, 50, 1, 0]], np.float64)
    order = L.solve_open(cost, [0], tmp_path, 5)           # 0 is the cheap end, but forbidden
    assert order[0] != 0 and order[-1] != 0
