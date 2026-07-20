"""Offline tests for app/api/transfer.py — mocks get_broker() so nothing
here constructs a real broker or touches the network."""

from unittest import mock

import pytest

from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class FakeBroker:
    def __init__(self):
        self.deposit_calls = []
        self.withdraw_calls = []

    def deposit(self, amount, **kwargs):
        self.deposit_calls.append((amount, kwargs))
        return {"id": "dep-1", "amount": amount, "state": "pending"}

    def withdraw(self, amount, **kwargs):
        self.withdraw_calls.append((amount, kwargs))
        return {"id": "wd-1", "amount": amount, "state": "pending"}

    def linked_bank_accounts(self):
        return [{"id": "b1"}]

    def transfer_history(self, **kwargs):
        return [{"id": "t1"}]


class UnsupportedBroker:
    def deposit(self, amount, **kwargs):
        raise NotImplementedError("IBKR does not support deposits")

    def withdraw(self, amount, **kwargs):
        raise NotImplementedError("IBKR does not support withdrawals")

    def linked_bank_accounts(self):
        raise NotImplementedError("IBKR does not support linked bank accounts")

    def transfer_history(self, **kwargs):
        raise NotImplementedError("IBKR does not support transfer history")


def test_deposit_dry_run_does_not_call_broker(client):
    with mock.patch("app.api.transfer.get_broker") as gb:
        resp = client.post("/api/transfer/deposit/robinhood", json={"amount": 100})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "simulated"
    gb.assert_not_called()


def test_deposit_rejects_non_positive_amount(client):
    resp = client.post("/api/transfer/deposit/robinhood", json={"amount": 0, "dry_run": False})
    assert resp.status_code == 400


def test_deposit_submits_when_not_dry_run(client):
    fake = FakeBroker()
    with mock.patch("app.api.transfer.get_broker", return_value=fake):
        resp = client.post("/api/transfer/deposit/robinhood",
                            json={"amount": 100, "ach_relationship": "ach-1", "dry_run": False})
    assert resp.status_code == 201
    assert resp.get_json()["status"] == "submitted"
    assert fake.deposit_calls == [(100.0, {"ach_relationship": "ach-1"})]


def test_deposit_unsupported_broker_returns_501(client):
    with mock.patch("app.api.transfer.get_broker", return_value=UnsupportedBroker()):
        resp = client.post("/api/transfer/deposit/ibkr", json={"amount": 100, "dry_run": False})
    assert resp.status_code == 501
    assert resp.get_json()["supported"] is False


def test_withdraw_submits_when_not_dry_run(client):
    fake = FakeBroker()
    with mock.patch("app.api.transfer.get_broker", return_value=fake):
        resp = client.post("/api/transfer/withdraw/robinhood",
                            json={"amount": 40, "ach_relationship": "ach-1", "dry_run": False})
    assert resp.status_code == 201
    assert fake.withdraw_calls == [(40.0, {"ach_relationship": "ach-1"})]


def test_bank_accounts_unsupported_broker_returns_501(client):
    with mock.patch("app.api.transfer.get_broker", return_value=UnsupportedBroker()):
        resp = client.get("/api/transfer/bank-accounts/ibkr")
    assert resp.status_code == 501


def test_between_validates_broker_names(client):
    resp = client.post("/api/transfer/between", json={
        "from_broker": "robinhood", "to_broker": "robinhood", "amount": 10, "dry_run": False,
    })
    assert resp.status_code == 400


def test_between_dry_run(client):
    resp = client.post("/api/transfer/between", json={
        "from_broker": "robinhood", "to_broker": "ibkr", "amount": 50,
    })
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "simulated"


def test_between_partial_when_destination_unsupported(client):
    fake_rh = FakeBroker()
    fake_ibkr = UnsupportedBroker()

    def fake_get_broker(name):
        return fake_rh if name == "robinhood" else fake_ibkr

    with mock.patch("app.api.transfer.get_broker", side_effect=fake_get_broker):
        resp = client.post("/api/transfer/between", json={
            "from_broker": "robinhood", "to_broker": "ibkr", "amount": 50, "dry_run": False,
        })
    assert resp.status_code == 207
    body = resp.get_json()
    assert body["status"] == "partial"
    assert body["withdrawal"]["id"] == "wd-1"
    assert fake_rh.withdraw_calls == [(50.0, {"ach_relationship": None})]


def test_between_submits_both_legs_when_both_supported(client):
    fake_rh, fake_other = FakeBroker(), FakeBroker()

    def fake_get_broker(name):
        return fake_rh if name == "robinhood" else fake_other

    with mock.patch("app.api.transfer.get_broker", side_effect=fake_get_broker):
        resp = client.post("/api/transfer/between", json={
            "from_broker": "robinhood", "to_broker": "ibkr", "amount": 50, "dry_run": False,
        })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["status"] == "submitted"
    assert body["withdrawal"]["id"] == "wd-1"
    assert body["deposit"]["id"] == "dep-1"
