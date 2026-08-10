"""Order lookup / patch (replace) / cancel — client helpers + API routes.

No network: an in-memory FakeBox simulates the auth-service, including RH's
replace semantics (a replace returns a NEW order id whose `replaces` points at
the old one). What's under test is the walk loop's id-chaining and convergence,
the replace-payload builder, and the three new proxy routes.
"""

import pytest
from flask import Flask

from app.api import register_blueprints
from app.auth_service_client import build_replace_payload, walk_order


def base_order(price=191.28, oid="ord-0"):
    return {
        "id": oid,
        "account": "https://api.robinhood.com/accounts/5RX91619/",
        "instrument": "https://api.robinhood.com/instruments/c81e8b9e/",
        "symbol": "NBIS", "type": "limit", "side": "buy", "quantity": "15",
        "time_in_force": "gtc", "trigger": "immediate", "price": str(price),
        "state": "queued", "is_editable": True,
    }


class FakeBox:
    """In-memory stand-in for AuthServiceClient with RH replace semantics."""

    def __init__(self, order):
        self.orders = {order["id"]: order}
        self.replace_calls, self.cancel_calls = [], []
        self._n = 0

    def get_order(self, order_id):
        return dict(self.orders[order_id])

    def replace_order(self, order_id, payload, dry_run=True):
        self.replace_calls.append((order_id, payload, dry_run))
        if dry_run:
            return {"dry_run": True, "order_id": order_id, "payload": payload}
        self._n += 1
        new_id = f"ord-{self._n}"
        old = dict(self.orders[order_id])
        old["state"], old["is_editable"] = "cancelled", False
        self.orders[order_id] = old
        new = {**base_order(float(payload["price"]), new_id),
               "replaces": order_id}
        self.orders[new_id] = new
        return new

    def cancel_order(self, order_id, dry_run=True):
        self.cancel_calls.append((order_id, dry_run))
        if not dry_run:
            self.orders[order_id]["state"] = "cancelled"
            self.orders[order_id]["is_editable"] = False
        return {"cancelled": order_id, "dry_run": dry_run}


# -- build_replace_payload ---------------------------------------------------

def test_build_replace_payload_overrides_price_and_mints_ref_id():
    o = base_order(191.28)
    p = build_replace_payload(o, price=192.00)
    assert p["price"] == "192.0"
    assert p["account"] == o["account"] and p["instrument"] == o["instrument"]
    assert p["ref_id"] and p["ref_id"] != o.get("ref_id")
    # never carry server-owned fields into a replace
    assert "id" not in p and "state" not in p and "is_editable" not in p


def test_build_replace_payload_keeps_price_when_omitted():
    p = build_replace_payload(base_order(191.28))
    assert p["price"] == "191.28"


# -- walk_order --------------------------------------------------------------

def test_walk_converges_and_chains_replacement_ids():
    box = FakeBox(base_order(191.28, "ord-0"))
    steps = walk_order(box, "ord-0", 191.53, step=0.05, dry_run=False)
    assert [s["to"] for s in steps] == [191.33, 191.38, 191.43, 191.48, 191.53]
    # each step targets the id returned by the previous replace, not the original
    assert [s["order_id"] for s in steps] == \
        ["ord-0", "ord-1", "ord-2", "ord-3", "ord-4"]
    assert float(box.orders["ord-5"]["price"]) == 191.53


def test_walk_dry_run_does_one_step_only():
    box = FakeBox(base_order(191.28))
    steps = walk_order(box, "ord-0", 192.70, dry_run=True)
    assert len(steps) == 1 and steps[0]["to"] == 191.33
    assert box.replace_calls[0][2] is True


def test_walk_stops_when_not_editable():
    o = base_order(191.28)
    o["is_editable"] = False
    steps = walk_order(FakeBox(o), "ord-0", 192.70, dry_run=False)
    assert steps == []


def test_walk_downward_toward_lower_target():
    box = FakeBox(base_order(191.50, "ord-0"))
    steps = walk_order(box, "ord-0", 191.40, step=0.05, dry_run=False)
    assert [s["to"] for s in steps] == [191.45, 191.40]


# -- API routes --------------------------------------------------------------

class FakeClient:
    def get_order(self, oid):
        return {"id": oid, "price": "191.28", "state": "queued"}

    def replace_order(self, oid, payload, dry_run=True):
        return {"id": "new-1", "replaces": oid, "dry_run": dry_run,
                "price": payload.get("price")}

    def cancel_order(self, oid, dry_run=True):
        return {"cancelled": oid, "dry_run": dry_run}


@pytest.fixture
def api(monkeypatch):
    app = Flask(__name__)
    register_blueprints(app)
    app.config["DRY_RUN"] = True
    monkeypatch.setattr("app.api.robinhood_proxy.AuthServiceClient",
                        lambda *a, **k: FakeClient())
    return app.test_client()


def test_get_order_route(api):
    r = api.get("/api/robinhood/orders/abc")
    assert r.status_code == 200 and r.get_json()["id"] == "abc"


def test_replace_route_requires_payload(api):
    r = api.post("/api/robinhood/orders/abc/replace", json={})
    assert r.status_code == 400


def test_replace_route_relays_payload(api):
    r = api.post("/api/robinhood/orders/abc/replace",
                 json={"payload": {"price": "192.00"}, "dry_run": True})
    body = r.get_json()
    assert r.status_code == 200 and body["replaces"] == "abc"
    assert body["price"] == "192.00"


def test_cancel_route(api):
    r = api.post("/api/robinhood/orders/abc/cancel", json={"dry_run": True})
    assert r.status_code == 200 and r.get_json()["cancelled"] == "abc"
