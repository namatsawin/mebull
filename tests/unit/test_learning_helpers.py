from apm.learning.evaluator import _is_ticker


def test_real_tickers_pass():
    for s in ["SPY", "QQQ", "IWM", "AAPL", "BRK.B"]:
        assert _is_ticker(s), s


def test_non_tickers_rejected():
    # Action/state words Claude may list among considered opportunities.
    for s in ["WAIT", "HOLD", "CASH", "NONE", "BUY", "SELL", "wait", " cash "]:
        assert not _is_ticker(s), s


def test_junk_rejected():
    for s in ["", "TOOLONGSYMBOL", "12345", "A B"]:
        assert not _is_ticker(s), s
