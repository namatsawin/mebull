from apm.decision.context_builder import ContextBuilder
from apm.memory.service import MemoryService
from apm.webull.mock import MockWebullAdapter


async def test_affordable_options_filters_by_buying_power():
    adapter = MockWebullAdapter()
    adapter.set_quote("SPY", 100.0)
    cb = ContextBuilder(adapter, MemoryService("p"))

    res = await cb._affordable_options(["SPY"], buying_power=100_000)
    assert "SPY" in res and res["SPY"]
    for c in res["SPY"]:
        assert c["cost"] <= 100_000
        assert c["right"] in ("CALL", "PUT")
    # sorted cheapest-first
    costs = [c["cost"] for c in res["SPY"]]
    assert costs == sorted(costs)


async def test_no_affordable_options_when_broke():
    adapter = MockWebullAdapter()
    adapter.set_quote("SPY", 100.0)
    cb = ContextBuilder(adapter, MemoryService("p"))
    res = await cb._affordable_options(["SPY"], buying_power=1.0)  # can't afford any contract
    assert res == {}
