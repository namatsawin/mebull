from apm.orchestrator.main import seconds_to_next_boundary


def test_aligns_to_next_boundary():
    # 300s interval: boundary every 5 minutes of epoch time (:00/:05/:10 ... UTC).
    assert seconds_to_next_boundary(300, 1000.0) == 200.0   # 1000 % 300 = 100 -> 200
    assert seconds_to_next_boundary(300, 1234.0) == 266.0   # 1234 % 300 = 34  -> 266


def test_boundary_math_is_exact():
    assert seconds_to_next_boundary(300, 1234.0) == 300 - (1234 % 300)  # 266.0


def test_exactly_on_boundary_waits_full_interval():
    assert seconds_to_next_boundary(300, 1500.0) == 300.0   # 1500 % 300 == 0
    assert seconds_to_next_boundary(300, 0.0) == 300.0


def test_overrun_coalesces_to_next_boundary():
    # A cycle that finishes 10s into a window still targets the next boundary, not a
    # back-to-back run of the missed ones.
    assert seconds_to_next_boundary(300, 1510.0) == 290.0   # 1510 % 300 = 10 -> 290


def test_various_intervals():
    assert seconds_to_next_boundary(60, 1000.0) == 20.0     # 1000 % 60 = 40 -> 20
    assert seconds_to_next_boundary(900, 1000.0) == 800.0   # 1000 % 900 = 100 -> 800
