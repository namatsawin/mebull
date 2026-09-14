from apm.observability.logging import _redact_processor


def test_sensitive_keys_are_redacted():
    event = {"event": "webull.auth", "app_secret": "super-secret", "app_key": "abc123"}
    out = _redact_processor(None, "info", event)
    assert out["app_secret"] == "***redacted***"
    assert out["app_key"] == "***redacted***"


def test_token_pattern_scrubbed_from_message():
    event = {"event": "calling api with sk-abcdef123456 now"}
    out = _redact_processor(None, "info", event)
    assert "sk-abcdef123456" not in out["event"]
    assert "***redacted***" in out["event"]


def test_non_sensitive_fields_preserved():
    event = {"event": "heartbeat", "portfolio_id": "main-portfolio"}
    out = _redact_processor(None, "info", event)
    assert out["portfolio_id"] == "main-portfolio"
    assert out["event"] == "heartbeat"
