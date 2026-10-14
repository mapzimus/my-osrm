"""Open-path asymmetric TSP with LKH-3 (Docker image walmart-lkh).

Free start and end: a dummy node 0 costs nothing to or from every stop,
except stops in no_ends (island stops: a round trip by plane can't be where
the trip starts or ends), which cost NO_END to or from the dummy. The tour
LKH returns is rotated to begin after the dummy, which is then dropped."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

IMAGE = "walmart-lkh"
LIMIT = 50_000_000      # LKH works in int; ATSP doubles the graph and adds big constants: keep headroom
NO_END = 20_000_000


def to_int(cost: np.ndarray) -> np.ndarray:
    c = np.rint(cost)
    if np.nanmax(c) >= LIMIT:
        raise ValueError(f"cost {np.nanmax(c):.0f} >= LKH limit {LIMIT}")
    return c.astype(np.int32)


def write_problem(cost: np.ndarray, no_ends: list[int], path: Path) -> None:
    n = len(cost)
    full = np.zeros((n + 1, n + 1), np.int32)
    full[1:, 1:] = to_int(cost)
    for k in no_ends:
        full[0, k + 1] = full[k + 1, 0] = NO_END
    with open(path, "w") as f:
        f.write(f"NAME: walmart\nTYPE: ATSP\nDIMENSION: {n + 1}\nEDGE_WEIGHT_TYPE: EXPLICIT\n"
                "EDGE_WEIGHT_FORMAT: FULL_MATRIX\nEDGE_WEIGHT_SECTION\n")
        np.savetxt(f, full, fmt="%d")
        f.write("EOF\n")


def read_order(path: Path) -> list[int]:
    lines = path.read_text().split("TOUR_SECTION")[1].split()
    tour = [int(v) for v in lines if v not in ("-1", "EOF")]
    k = tour.index(1)
    return [v - 2 for v in tour[k + 1:] + tour[:k]]


def solve_open(cost: np.ndarray, no_ends: list[int], workdir: Path, seconds: int) -> list[int]:
    workdir.mkdir(parents=True, exist_ok=True)
    write_problem(cost, no_ends, workdir / "p.atsp")
    (workdir / "p.par").write_text(
        "PROBLEM_FILE = /w/p.atsp\nOUTPUT_TOUR_FILE = /w/p.tour\nRUNS = 1\nSEED = 1\n"
        f"TIME_LIMIT = {seconds}\nPRECISION = 1\nTRACE_LEVEL = 1\n")
    with open(workdir / "lkh.log", "w") as log:      # LKH's trace; also no console when run detached
        subprocess.run(["docker", "run", "--rm", "-v", f"{workdir.resolve()}:/w", IMAGE, "/w/p.par"],
                       check=True, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    return read_order(workdir / "p.tour")
