"""Research + Backtesting (spec §7, §26-29, §51). Milestone M8.

Historical research, backtesting (no look-ahead, point-in-time data, realistic
execution/slippage/fees), counterfactual and strategy-lifecycle analysis. Research is
NOT trading (spec §7): it produces evidence and MUST NEVER modify the real account (spec §29).
"""

from apm.research.backtest import (
    BacktestConfig,
    BacktestResult,
    always_long,
    run_backtest,
    sma_crossover,
)
from apm.research.experiment import ExperimentService
from apm.research.strategy import StrategyService

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "run_backtest",
    "always_long",
    "sma_crossover",
    "StrategyService",
    "ExperimentService",
]
