"""Offline tests for app/api/transfer.py — dry-run/armed gating and the
withdraw-then-deposit orchestration, with fake brokers standing in for
Robinhood/IBKR (no real ACH calls).
"""

import pytest

from app import create_app
from app.config import Config
import app.api.transfer as transfer_mod


class FakeBroker:
    def __init__(self, deposit_result=None, withdraw_result=None,
                 deposit_error=None, withdraw_error=None, supports=("deposit", "withdraw")):
        self._deposit_result = deposit_result or {"id": "dep-1"}
        self._withdraw_result = withdraw_result or {"id": "wd-1"}
        self._deposit_error = deposit_error
        self._withdraw_error = withdraw_error
        self._supports = supports

    def deposit(self, amount):
        if "deposit" not in self._supports:
            # Mirrors IBKRTrader.deposit(): the method exists but the
            # broker has no retail funding API, so it fails loudly.
            raise NotImplementedError("no funding API for this broker")
        if self._deposit_error:
            raise self._deposit_error
        return self._deposit_result

    def withdraw(self, amount):
        if "withdraw" not in self._supports:
            raise NotImplementedError("no funding API for this broker")
        if self._withdraw_error:
            raise self._withdraw_error
        return self._withdraw_result


@pytest.fixture
def app():
    class TestConfig(Config):
        TESTING = True
        TRANSFERS_DRY_RUN = True

    application = create_app(TestConfig)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def fake_brokers(monkeypatch):
    brokers = {"robinhood": FakeBroker(), "ibkr": FakeBroker(supports=())}

    def fake_get_broker(name):
        return brokers[name]

    monkeypatch.setattr(transfer_mod, "get_broker", fake_get_broker)
    return brokers


# --------------------------------------------------------------------------- #
# dry-run / armed gating
# --------------------------------------------------------------------------- #

def test_transfer_defaults_to_dry_run(client, fake_brokers):
    resp = client.post("/api/transfer", json={
        "from_broker": "robinhood", "to_broker": "ibkr", "amount": 500,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "simulated"
    assert body["dry_run"] is True


def test_transfer_live_without_armed_is_rejected(client, fake_brokers):
    resp = client.post("/api/transfer", json={
        "from_broker": "robinhood", "to_broker": "ibkr", "amount": 500,
        "dry_run": False,
    })
    assert resp.status_code == 400
    assert "armed" in resp.get_json()["error"]


def test_transfer_requires_different_brokers(client, fake_brokers):
    resp = client.post("/api/transfer", json={
        "from_broker": "robinhood", "to_broker": "robinhood", "amount": 500,
    })
    assert resp.status_code == 400


def test_transfer_rejects_non_positive_amount(client, fake_brokers):
    resp = client.post("/api/transfer", json={
        "from_broker": "robinhood", "to_broker": "ibkr", "amount": 0,
    })
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# orchestration: withdraw then deposit, and the "not rolled back" case
# --------------------------------------------------------------------------- #

def test_transfer_armed_calls_withdraw_then_deposit(client, monkeypatch):
    robinhood = FakeBroker(withdraw_result={"id": "wd-42"})
    ibkr = FakeBroker(deposit_result={"id": "dep-42"})
    monkeypatch.setattr(transfer_mod, "get_broker",
                         lambda name: {"robinhood": robinhood, "ibkr": ibkr}[name])

    resp = client.post("/api/transfer", json={
        "from_broker": "robinhood", "to_broker": "ibkr", "amount": 250,
        "dry_run": False, "armed": True,
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["withdraw_result"]["id"] == "wd-42"
    assert body["deposit_result"]["id"] == "dep-42"


def test_transfer_deposit_leg_failure_is_reported_not_hidden(client, monkeypatch):
    robinhood = FakeBroker(withdraw_result={"id": "wd-99"})
    ibkr = FakeBroker(supports=())  # no deposit support -> NotImplementedError path

    def fake_get_broker(name):
        return {"robinhood": robinhood, "ibkr": ibkr}[name]

    monkeypatch.setattr(transfer_mod, "get_broker", fake_get_broker)

    resp = client.post("/api/transfer", json={
        "from_broker": "robinhood", "to_broker": "ibkr", "amount": 250,
        "dry_run": False, "armed": True,
    })
    assert resp.status_code == 501
    body = resp.get_json()
    assert body["leg"] == "deposit"
    assert body["withdraw_result"]["id"] == "wd-99"
    assert "rolled back" in body["warning"]


# --------------------------------------------------------------------------- #
# single-leg endpoints
# --------------------------------------------------------------------------- #

def test_single_leg_deposit_dry_run(client, fake_brokers):
    resp = client.post("/api/transfer/deposit/robinhood", json={"amount": 100})
    assert resp.status_code == 200
    assert resp.get_json()["dry_run"] is True


def test_single_leg_deposit_unsupported_broker_returns_501(client, fake_brokers):
    resp = client.post("/api/transfer/deposit/ibkr", json={
        "amount": 100, "dry_run": False, "armed": True,
    })
    assert resp.status_code == 501
    assert "no funding API" in resp.get_json()["error"]
