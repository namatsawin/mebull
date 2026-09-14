from fastapi.testclient import TestClient

from apm.orchestrator.health import create_health_app


def test_health_liveness_ok():
    client = TestClient(create_health_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_status_reports_no_secrets():
    client = TestClient(create_health_app())
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    # Safe operational fields only — never credentials.
    assert "portfolio_id" in body
    assert "execution_mode" in body
    assert "anthropic_api_key" not in body
    assert "webull_app_secret" not in body
