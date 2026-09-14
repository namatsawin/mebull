import datetime as dt

from apm.domain import Bar
from apm.research.backtest import (
    BacktestConfig,
    always_long,
    run_backtest,
    sma_crossover,
)


def _bars(closes: list[float]) -> list[Bar]:
    base = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    return [
        Bar(symbol="X", timestamp=base + dt.timedelta(days=i), open=c, high=c, low=c,
            close=c, volume=1000)
        for i, c in enumerate(closes)
    ]


def test_always_long_on_rising_series_profits():
    bars = _bars([100, 101, 102, 103, 104, 105])
    res = run_backtest(bars, always_long, BacktestConfig(slippage_bps=0, fee_bps=0))
    assert res.total_return > 0
    assert res.final_equity > 100_000


def test_always_long_on_falling_series_loses():
    bars = _bars([100, 99, 98, 97, 96])
    res = run_backtest(bars, always_long, BacktestConfig(slippage_bps=0, fee_bps=0))
    assert res.total_return < 0


def test_flat_strategy_no_pnl_no_drawdown():
    bars = _bars([100, 120, 80, 130, 90])
    res = run_backtest(bars, lambda h: 0.0)
    assert res.total_return == 0.0
    assert res.max_drawdown == 0.0
    assert res.sample_size == 0


def test_no_look_ahead_signal_uses_only_history():
    # A strategy that inspects the full list still only receives history up to i; assert the
    # engine never hands it a longer list than the current index + 1.
    seen_lengths: list[int] = []
    bars = _bars([100, 101, 102, 103])

    def spy(history):
        seen_lengths.append(len(history))
        return 0.0

    run_backtest(bars, spy)
    assert seen_lengths == [1, 2, 3, 4]  # strictly point-in-time


def test_sma_crossover_runs_and_reports():
    closes = [100 + (i % 7) - 3 for i in range(80)]  # choppy
    res = run_backtest(_bars(closes), sma_crossover(5, 20))
    assert res.bars == 80
    assert res.max_drawdown >= 0.0
    assert res.look_ahead_free is True
