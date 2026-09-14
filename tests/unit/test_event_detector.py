from apm.events.detector import Event, EventDetector, EventType


def test_scheduled_events_always_meaningful():
    d = EventDetector()
    for et in (EventType.MARKET_OPEN_REVIEW, EventType.WEEKLY_REVIEW):
        assert d.is_meaningful(Event(type=et))


def test_portfolio_events_always_meaningful():
    d = EventDetector()
    assert d.is_meaningful(Event(type=EventType.ORDER_FILLED, symbol="NVDA"))
    assert d.is_meaningful(Event(type=EventType.ORDER_REJECTED))


def test_price_move_threshold_gate():
    d = EventDetector(price_move_pct=3.0)
    assert not d.is_meaningful(
        Event(type=EventType.LARGE_PRICE_MOVE, symbol="NVDA", payload={"change_pct": 1.2})
    )
    assert d.is_meaningful(
        Event(type=EventType.LARGE_PRICE_MOVE, symbol="NVDA", payload={"change_pct": 4.5})
    )


def test_volume_spike_and_news_gates():
    d = EventDetector(volume_spike_ratio=3.0)
    assert not d.is_meaningful(Event(type=EventType.VOLUME_SPIKE, payload={"ratio": 1.5}))
    assert d.is_meaningful(Event(type=EventType.VOLUME_SPIKE, payload={"ratio": 5.0}))
    assert not d.is_meaningful(Event(type=EventType.NEWS_EVENT, payload={"high_impact": False}))
    assert d.is_meaningful(Event(type=EventType.NEWS_EVENT, payload={"high_impact": True}))
