"""POST /api/signals/{buy,sell}: notify-only handler chain keyed by caller GUID."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

import app.signals as signals
from app.api import register_blueprints

TOKEN = "t0ken"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def sig(**over):
    body = {"signal_id": str(uuid.uuid4()), "symbol": "mu", "quantity": 2,
            "source": "ext-bot", "reason": "rsi>80"}
    body.update(over)
    return body


@pytest.fixture
def env(monkeypatch):
    alerts = []
    monkeypatch.setattr(signals, "notify", lambda m, **k: alerts.append(m))
    signals.MEMORY.clear()

    def client(**cfg):
        app = Flask(__name__)
        register_blueprints(app)
        app.config.update(DASHBOARD_REQUEST_TOKEN=TOKEN, **cfg)
        return app.test_client()
    yield client, alerts
    signals.MEMORY.clear()


def test_alert_carries_guid_and_result_echoes_it(env):
    client, alerts = env
    body = sig()
    r = client().post("/api/signals/sell", json=body)
    out = r.get_json()
    assert r.status_code == 202
    assert out["signal_id"] == body["signal_id"] and out["status"] == "alerted"
    assert out["handlers"] == ["dedupe", "freshness", "cooldown", "alert"]
    assert len(alerts) == 1 and f"id={body['signal_id']}" in alerts[0]
    assert alerts[0].startswith("[sell-signal] SELL 2 MU @ market from ext-bot [UNAUTHORIZED]")


def test_buy_with_limit_and_hours_and_token(env):
    client, alerts = env
    r = client().post("/api/signals/buy", headers=AUTH,
                      json=sig(limit_price=950, market_hours="extended_hours"))
    assert r.get_json()["authorized"] is True
    assert "BUY 2 MU @ 950.0 (extended_hours)" in alerts[0] and "[authorized]" in alerts[0]


@pytest.mark.parametrize("bad_id", [None, "", "not-a-guid", "123"])
def test_signal_id_required_guid(env, bad_id):
    client, alerts = env
    body = sig()
    body["signal_id"] = bad_id
    assert client().post("/api/signals/sell", json=body).status_code == 400
    assert alerts == []


def test_duplicate_guid_returns_original_without_new_alert(env):
    client, alerts = env
    body = sig()
    first = client().post("/api/signals/sell", json=body).get_json()
    again = client().post("/api/signals/sell", json=body).get_json()
    assert again["duplicate"] is True and again["signal_id"] == first["signal_id"]
    assert again["status"] == first["status"] and len(alerts) == 1


def test_cooldown_suppresses_same_side_symbol_then_reports_count(env, monkeypatch):
    client, alerts = env
    c = client(SIGNAL_COOLDOWN_SECONDS=300)
    assert c.post("/api/signals/sell", json=sig()).get_json()["status"] == "alerted"
    for n in (1, 2):
        out = c.post("/api/signals/sell", json=sig()).get_json()
        assert out["status"] == "suppressed" and out["repeats_in_window"] == n
    assert len(alerts) == 1
    # opposite side and other symbols are independent
    assert c.post("/api/signals/buy", json=sig()).get_json()["status"] == "alerted"
    assert c.post("/api/signals/sell", json=sig(symbol="NBIS")).get_json()["status"] == "alerted"
    # after the window, the next alert reports what was suppressed
    real = signals.time.time
    monkeypatch.setattr(signals.time, "time", lambda: real() + 301)
    assert c.post("/api/signals/sell", json=sig()).get_json()["status"] == "alerted"
    assert "(+2 repeats suppressed)" in alerts[-1]


def test_expired_signal_is_alerted_as_expired_and_does_not_cool(env):
    client, alerts = env
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    out = client().post("/api/signals/sell", json=sig(expires_at=past)).get_json()
    assert out["status"] == "expired" and "EXPIRED" in alerts[-1]
    assert client().post("/api/signals/sell", json=sig()).get_json()["status"] == "alerted"


def test_default_expiry_is_ttl_after_receipt(env):
    client, _ = env
    out = client(SIGNAL_TTL_SECONDS=300).post("/api/signals/sell", json=sig()).get_json()
    exp = datetime.fromisoformat(out["expires_at"])
    left = (exp - datetime.now(timezone.utc)).total_seconds()
    assert 290 < left <= 300


def test_unknown_listener_fails_loudly(env):
    client, alerts = env
    r = client(SIGNAL_HANDLERS="alert,bogus").post("/api/signals/sell", json=sig())
    assert r.status_code == 500 and "unknown SIGNAL_HANDLERS" in r.get_json()["error"]
    assert alerts == []


def test_unknown_side_is_404(env):
    client, alerts = env
    assert client().post("/api/signals/short", json=sig()).status_code == 404
    assert alerts == []


@pytest.mark.parametrize("over", [{"symbol": "M;U"}, {"quantity": -1}, {"quantity": "x"},
                                  {"quantity": "inf"}, {"limit_price": 0},
                                  {"market_hours": "weekend"}, {"expires_at": "soon"}])
def test_bad_input_is_400(env, over):
    client, alerts = env
    assert client().post("/api/signals/sell", json=sig(**over)).status_code == 400
    assert alerts == []


SPREAD = {"direction": "credit", "type": "limit", "price": "20.00", "quantity": "1",
          "ref_id": "5f1d6a52-6d1b-4a39-9a43-0f0e9a8b1c11",
          "legs": [{"option": "a", "side": "sell", "position_effect": "close"},
                   {"option": "b", "side": "buy", "position_effect": "close"}]}


@pytest.fixture
def relay(monkeypatch):
    import app.auth_service_client as asc
    calls = []

    def fake(self, method, path, body=None, dry_run=True):
        calls.append((method, path, body, dry_run))
        if isinstance(fake.result, Exception):
            raise fake.result
        return fake.result
    fake.result = {"id": "rh-1", "state": "queued"}
    monkeypatch.setattr(asc.AuthServiceClient, "relay", fake)
    return calls, fake


def test_authorized_order_is_relayed_and_alerted(env, relay):
    client, alerts = env
    calls, _ = relay
    r = client(SIGNAL_HANDLERS="alert,execute", DRY_RUN=False).post(
        "/api/signals/sell", headers=AUTH, json=sig(symbol="AMD", quantity=1, order=SPREAD))
    out = r.get_json()
    assert calls == [("POST", "/options/orders/", SPREAD, False)]
    assert out["order"] == {"sent": True, "path": "/options/orders/", "state": "queued",
                            "order_id": "rh-1"}
    assert len(alerts) == 2 and alerts[1].startswith("[sell-order]") and "queued" in alerts[1]


def test_order_not_relayed_without_token_or_execute_handler(env, relay):
    client, alerts = env
    calls, _ = relay
    out = client(SIGNAL_HANDLERS="alert,execute").post(
        "/api/signals/sell", json=sig(order=SPREAD)).get_json()
    assert out["order"] == {"sent": False, "why": "unauthorized"}
    client().post("/api/signals/sell", headers=AUTH, json=sig(symbol="nvda", order=SPREAD))
    assert calls == []


def test_relay_failure_is_alerted(env, relay):
    from app.auth_service_client import AuthServiceError
    client, alerts = env
    _, fake = relay
    fake.result = AuthServiceError("403 GUARDRAIL_BLOCKED")
    out = client(SIGNAL_HANDLERS="alert,execute").post(
        "/api/signals/sell", headers=AUTH, json=sig(order=SPREAD)).get_json()
    assert out["order"]["sent"] is False and "GUARDRAIL" in out["order"]["error"]
    assert "FAILED" in alerts[-1]


def test_order_must_be_object(env):
    client, _ = env
    assert client().post("/api/signals/sell", json=sig(order="sell it")).status_code == 400
