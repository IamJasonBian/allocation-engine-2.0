"""Smoke: the service imports, builds its API surface, and reports healthy."""

import pytest

pytestmark = pytest.mark.smoke


def test_health_reports_ok(api_client):
    r = api_client.get("/api/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert body["service"] == "allocation-engine-2.0"
    assert {"dry_run", "engine_enabled", "enabled_brokers", "timestamp"} <= body.keys()


def test_auth_status_never_500s_unconfigured(api_client, monkeypatch):
    # With no box configured the endpoint must degrade to "not authenticated",
    # not raise — the dashboard polls it.
    from app.config import Config
    monkeypatch.setattr(Config, "AUTH_SERVICE_URL", "")
    r = api_client.get("/api/auth/status")
    assert r.status_code < 500
    assert "authenticated" in r.get_json()


def test_engine_modules_import():
    import app.background  # noqa: F401
    import app.engine  # noqa: F401
    import app.trading_db  # noqa: F401
