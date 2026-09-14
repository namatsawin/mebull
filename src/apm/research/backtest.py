"""Backtesting (spec §26-29) — research, NOT trading.

Point-in-time evaluation with strict no-look-ahead: at bar i the strategy sees only
``bars[: i + 1]`` and its signal governs the position held during bar i+1 (filled on the
next bar's move). Realistic per-turnover slippage/fees are applied. Produces EVIDENCE only —
this module NEVER touches the broker or the account (spec §29).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from apm.domain import Bar

# A strategy sees history up to and including the current bar and returns the desired
# exposure for the NEXT bar: -1 (short), 0 (flat), or +1 (long). No future data is available.
Strategy = Callable[[list[Bar]], float]


@dataclass
class BacktestConfig:
    slippage_bps: float = 2.0
    fee_bps: float = 0.0
    initial_equity: float = 100_000.0


class BacktestResult(BaseModel):
    sample_size: int          # number of closed legs
    bars: int
    win_rate: float | None
    expectancy: float | None  # mean per-leg return
    profit_factor: float | None
    max_drawdown: float
    total_return: float
    final_equity: float
    look_ahead_free: bool = True


def run_backtest(
    bars: list[Bar], strategy: Strategy, config: BacktestConfig | None = None
) -> BacktestResult:
    config = config or BacktestConfig()
    n = len(bars)
    if n < 2:
        return BacktestResult(
            sample_size=0, bars=n, win_rate=None, expectancy=None, profit_factor=None,
            max_drawdown=0.0, total_return=0.0, final_equity=config.initial_equity,
        )

    # 1. Signals: exposure[i] decided from bars[: i+1] only (no look-ahead).
    exposures = [0.0] * n
    for i in range(n):
        exposures[i] = max(-1.0, min(1.0, float(strategy(bars[: i + 1]))))

    # 2. Walk forward: position held during bar i was decided at i-1.
    equity = config.initial_equity
    peak = equity
    max_dd = 0.0
    leg_returns: list[float] = []
    entry_price: float | None = None
    entry_dir = 0.0
    turn_cost = (config.slippage_bps + config.fee_bps) / 10_000.0

    for i in range(1, n):
        held = exposures[i - 1]
        r = bars[i].close / bars[i - 1].close - 1.0
        equity *= 1.0 + held * r

        # Turnover between the position held into bar i and the one decided at bar i.
        turnover = abs(exposures[i] - held)
        if turnover:
            equity *= 1.0 - turn_cost * turnover

        # Track per-leg returns for win/loss stats.
        if held != 0 and entry_price is None:
            entry_price, entry_dir = bars[i - 1].close, held
        if entry_price is not None and exposures[i] != held:
            leg_returns.append((bars[i].close / entry_price - 1.0) * entry_dir)
            entry_price = None if exposures[i] == 0 else bars[i].close
            entry_dir = exposures[i]

        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    wins = [x for x in leg_returns if x > 0]
    losses = [x for x in leg_returns if x < 0]
    m = len(leg_returns)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return BacktestResult(
        sample_size=m,
        bars=n,
        win_rate=(len(wins) / m) if m else None,
        expectancy=(sum(leg_returns) / m) if m else None,
        profit_factor=(gross_win / gross_loss) if gross_loss else None,
        max_drawdown=max_dd,
        total_return=equity / config.initial_equity - 1.0,
        final_equity=equity,
    )


# --- example strategies (research building blocks) --------------------------
def always_long(_history: list[Bar]) -> float:
    return 1.0


def sma_crossover(fast: int = 10, slow: int = 30) -> Strategy:
    """Long when the fast SMA is above the slow SMA, else flat. Uses only past closes."""

    def strat(history: list[Bar]) -> float:
        if len(history) < slow:
            return 0.0
        closes = [b.close for b in history]
        fast_ma = sum(closes[-fast:]) / fast
        slow_ma = sum(closes[-slow:]) / slow
        return 1.0 if fast_ma > slow_ma else 0.0

    return strat
