"""Rules in scripts/ferry_durations.py, on the real cases from the Oct 2026 scan.

    python -m pytest scripts/tests
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ferry_durations import FerryWay, mmss_fix, parse_duration, plan, stub_ways  # noqa: E402


def way(id_, name, dur, length, nodes):
    nodes = list(nodes)
    return FerryWay(id=id_, name=name, duration=dur, length_m=length, nodes=set(nodes),
                    ends=frozenset((nodes[0], nodes[-1])))


def test_parse_matches_osrm():
    assert parse_duration("01:00") == 3600
    assert parse_duration("0:02") == 120
    assert parse_duration("00:15:00") == 900
    assert parse_duration("15") == 900
    assert parse_duration("PT10M") == 600
    assert parse_duration("about an hour") is None
    assert parse_duration(None) is None


def test_mmss_typos_fixed():
    assert mmss_fix("60:00", 17_626) == "01:00:00"     # Kingstown - Bequia
    assert mmss_fix("30:00", 9_288) == "00:30:00"      # Daniel Island - Charleston
    assert mmss_fix("30:00", 21_688) == "00:30:00"     # Qaarsut - Uummannaq "(30 min)"
    assert mmss_fix("05:00", 300) == "00:05:00"        # Cruce a El Recreo


def test_mmss_leaves_real_durations():
    assert mmss_fix("01:00", 233) is None              # ambiguous: HH < 5
    assert mmss_fix("06:30", 462_642) is None          # Kodiak - Chignik really is 6.5 h
    assert mmss_fix("00:20", 161) is None              # minutes already
    assert mmss_fix("00:15:00", 556) is None           # HH:MM:SS form, unambiguous
    assert mmss_fix("PT10M", 452) is None
    assert mmss_fix("20:00", 10) is None               # 20 min for 10 m: still implausible, leave it


def test_bremerton_stub():
    main = way(5919117, "Seattle-Bremerton Ferry", "01:00", 25_072, [661057175, 1, 2, 2128433309])
    stub = way(1174688761, "Seattle-Bremerton Ferry", "01:00", 233, [2128433309, 648972987])
    other = way(1174688760, "Seattle-Bremerton Ferry", None, 237, [2128433309, 2061799540])
    assert stub_ways({w.id: w for w in (main, stub, other)}) == {1174688761: 5919117}


def test_jamestown_four_stubs():
    ws = [way(20327205, "J", "00:20", 3721, [215846660, 8307270611]),
          way(1222227956, "J", "00:20", 4176, [592660281, 11335645929]),
          way(1221205346, "J", "00:20", 168, [592660281, 215846660]),
          way(1221205347, "J", "00:20", 161, [215846660, 11322676479]),
          way(1222227955, "J", "00:20", 289, [8307270611, 11335645930]),
          way(1222227957, "J", "00:20", 306, [11335645931, 8307270611])]
    assert set(stub_ways({w.id: w for w in ws})) == {1221205346, 1221205347, 1222227955, 1222227957}


def test_parallel_lines_untouched():
    # South Ferry and DeTour - Drummond: two lines, same name and duration, separate ends
    a = way(17764027, "DeTour", "00:30:00", 1546, [2050217817, 184083892])
    b = way(1307167248, "DeTour", "00:30:00", 1554, [13017996291, 12103172091])
    assert stub_ways({a.id: a, b.id: b}) == {}
    # same endpoints (a duplicated line) is not a stub either, even if much shorter
    c = way(1, "X", "00:30", 100, [5, 9])
    d = way(2, "X", "00:30", 900, [5, 7, 8, 9])
    assert stub_ways({1: c, 2: d}) == {}


def test_long_piece_never_stripped():
    a = way(1, "Long", "02:00", 1500, [1, 2])          # over STUB_MAX_M
    b = way(2, "Long", "02:00", 90_000, [2, 3])
    assert stub_ways({1: a, 2: b}) == {}


def test_plan_combines_rules():
    main = way(5919117, "Seattle-Bremerton Ferry", "01:00", 25_072, [661057175, 2128433309])
    stub = way(1174688761, "Seattle-Bremerton Ferry", "01:00", 233, [2128433309, 648972987])
    bequia = way(839057557, "Kingstown - Bequia", "60:00", 17_626, [10, 11])
    changes = plan({w.id: w for w in (main, stub, bequia)})
    assert changes[1174688761]["duration"] is None
    assert changes[839057557]["duration"] == "01:00:00"
    assert 5919117 not in changes
