"""POST /api/signals/{buy,sell}: open intake, always alerts, only token-holders execute."""

import pytest
from flask import Flask

import app.signals as signals
from app.api import register_blueprints

TOKEN = "t0ken"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SIG = {"symbol": "mu", "quantity": 2, "source": "ext-bot", "reason": "rsi>80"}


class FakeBroker:
    def __init__(self, held=5.0, result=None, bp=1000.0):
        self.held, self.result, self.submitted, self.bp = held, result, [], bp

    def account(self):
        return {"buying_power": self.bp}

    def positions(self):
        return [{"symbol": "MU", "qty": self.held}]

    def submit_order(self, order):
        self.submitted.append(order)
        return self.result


@pytest.fixture
def env(monkeypatch):
    alerts, broker = [], FakeBroker(result={"id": "ord-1"})
    monkeypatch.setattr(signals, "notify", lambda m, **k: alerts.append(m))
    monkeypatch.setattr("app.api.signals.get_broker", lambda name: broker)

    def client(mode="execute", dry_run=False, token=TOKEN):
        app = Flask(__name__)
        register_blueprints(app)
        app.config.update(DEFAULT_BROKER="robinhood", DRY_RUN=dry_run,
                          SELL_SIGNAL_MODE=mode, DASHBOARD_REQUEST_TOKEN=token)
        return app.test_client()
    return client, alerts, broker


def test_unauthorized_signal_alerts_and_never_trades(env):
    client, alerts, broker = env
    r = client().post("/api/signals/sell", json=SIG)
    body = r.get_json()
    assert r.status_code == 202
    assert body["authorized"] is False and body["strategy"] == "alert_only"
    assert broker.submitted == []
    assert len(alerts) == 1 and "UNAUTHORIZED" in alerts[0] and "ext-bot" in alerts[0]


def test_wrong_token_is_unauthorized(env):
    client, _, broker = env
    r = client().post("/api/signals/sell", json=SIG, headers={"Authorization": "Bearer nope"})
    assert r.get_json()["authorized"] is False and broker.submitted == []


def test_unset_token_means_nobody_is_authorized(env):
    client, _, broker = env
    r = client(token="").post("/api/signals/sell", json=SIG, headers={"Authorization": "Bearer "})
    assert r.get_json()["authorized"] is False and broker.submitted == []


def test_authorized_alert_mode_does_not_trade(env):
    client, alerts, broker = env
    r = client(mode="alert").post("/api/signals/sell", json=SIG, headers=AUTH)
    assert r.get_json()["strategy"] == "alert_only" and broker.submitted == []
    assert alerts


def test_authorized_execute_submits_sell(env):
    client, alerts, broker = env
    r = client().post("/api/signals/sell", json={**SIG, "limit_price": 1000}, headers=AUTH)
    body = r.get_json()
    assert body["status"] == "submitted" and body["orderId"] == "ord-1"
    assert broker.submitted[0]["side"] == "SELL"
    assert broker.submitted[0]["order_type"] == "limit"
    assert broker.submitted[0]["symbol"] == "MU"
    assert "submitted ord-1" in alerts[-1]


def test_execute_respects_dry_run(env):
    client, _, broker = env
    r = client(dry_run=True).post("/api/signals/sell", json=SIG, headers=AUTH)
    assert r.get_json()["status"] == "simulated" and broker.submitted == []


def test_execute_refuses_to_oversell(env):
    client, alerts, broker = env
    r = client().post("/api/signals/sell", json={**SIG, "quantity": 99}, headers=AUTH)
    assert r.get_json()["executed"] is False and broker.submitted == []
    assert "held 5.0" in alerts[-1]


def test_broker_rejection_is_alerted(env):
    client, alerts, broker = env
    broker.result = None
    r = client().post("/api/signals/sell", json=SIG, headers=AUTH)
    assert r.get_json()["status"] == "rejected" and "REJECTED" in alerts[-1]


@pytest.mark.parametrize("bad", [{}, {"symbol": "MU"}, {"symbol": "MU", "quantity": -1},
                                 {"symbol": "M;U", "quantity": 1},
                                 {"symbol": "MU", "quantity": "x"},
                                 {"symbol": "MU", "quantity": "inf"},
                                 {"symbol": "MU", "quantity": 1, "limit_price": 0}])
def test_bad_input_is_400(env, bad):
    client, alerts, _ = env
    assert client().post("/api/signals/sell", json=bad).status_code == 400
    assert alerts == []


def test_unknown_side_is_404(env):
    client, alerts, _ = env
    assert client().post("/api/signals/short", json=SIG).status_code == 404
    assert alerts == []


def test_unauthorized_buy_alerts_and_never_trades(env):
    client, alerts, broker = env
    r = client().post("/api/signals/buy", json={**SIG, "limit_price": 100})
    body = r.get_json()
    assert r.status_code == 202 and body["strategy"] == "alert_only"
    assert broker.submitted == []
    assert alerts[-1].startswith("[buy-signal] BUY 2.0 MU @ 100.0") and "UNAUTHORIZED" in alerts[-1]


def test_authorized_buy_within_buying_power_submits(env):
    client, alerts, broker = env
    r = client().post("/api/signals/buy", json={**SIG, "limit_price": 100}, headers=AUTH)
    body = r.get_json()
    assert body["status"] == "submitted"
    assert broker.submitted[0]["side"] == "BUY" and broker.submitted[0]["order_type"] == "limit"
    assert "submitted ord-1" in alerts[-1]


def test_buy_over_buying_power_is_refused(env):
    client, alerts, broker = env
    r = client().post("/api/signals/buy", json={**SIG, "limit_price": 600}, headers=AUTH)
    assert r.get_json()["executed"] is False and broker.submitted == []
    assert "buying power 1000.00" in alerts[-1]


def test_market_buy_is_refused(env):
    client, alerts, broker = env
    r = client().post("/api/signals/buy", json=SIG, headers=AUTH)
    assert r.get_json()["executed"] is False and broker.submitted == []
    assert "need a limit_price" in alerts[-1]


def test_buy_dry_run_simulates(env):
    client, _, broker = env
    r = client(dry_run=True).post("/api/signals/buy", json={**SIG, "limit_price": 100}, headers=AUTH)
    assert r.get_json()["status"] == "simulated" and broker.submitted == []
