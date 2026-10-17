import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build as B  # noqa: E402

# Four stops on a line: 0 - 1 - 2 - 3. The best open path is 0,1,2,3 (or reversed).
LINE = [[0, 1, 2, 3], [1, 0, 1, 2], [2, 1, 0, 1], [3, 2, 1, 0]]


def test_last_forces_the_finish():
    B.SOLVE_SECONDS = 1
    order = B.solve(LINE, last=1)
    assert order[-1] == 1 and sorted(order) == [0, 1, 2, 3]


def test_no_ends_and_last_together():
    B.SOLVE_SECONDS = 1
    order = B.solve(LINE, no_ends=(0,), last=3)
    assert order[-1] == 3 and order[0] != 0
