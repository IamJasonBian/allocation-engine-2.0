"""Tests for the IBKR broker client (app.brokers.ibkr).

No network: the ibind IbkrClient and OAuth1aConfig are monkeypatched so
construction performs no handshake, and a FakeClient records/answers requests.
"""

import pytest

import app.brokers.ibkr.session as session_mod
from app.brokers.ibkr import IBKRTrader
from app.brokers.ibkr import contracts, orders, positions


class FakeResult:
    """Mimics ibind's Result object (has a .data attribute)."""

    def __init__(self, data):
        self.data = data


class Queue:
    """Wrap multiple sequential responses for the same path."""

    def __init__(self, *items):
        self.items = list(items)

    def next(self):
        if len(self.items) > 1:
            return self.items.pop(0)
        return self.items[0]


class FakeClient:
    """Records get/post/delete calls and returns scripted responses.

    `responses` maps a path (str) to a payload. The payload is returned as-is
    (so a list payload is delivered verbatim). To script multiple sequential
    responses for one path, wrap them in a ``Queue``.
    """

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []  # list of (verb, path, params)

    def _resolve(self, path):
        resp = self.responses.get(path, {})
        if isinstance(resp, Queue):
            return resp.next()
        return resp

    def get(self, path, params=None, **kw):
        self.calls.append(("GET", path, params))
        return FakeResult(self._resolve(path))

    def post(self, path, params=None, **kw):
        self.calls.append(("POST", path, params))
        return FakeResult(self._resolve(path))

    def delete(self, path, params=None, **kw):
        self.calls.append(("DELETE", path, params))
        return FakeResult(self._resolve(path))

    def tickle(self, log=False):
        self.calls.append(("TICKLE", "tickle", None))
        return FakeResult({"iserver": {"authStatus": {"authenticated": True}}})


@pytest.fixture
def patched_ibind(monkeypatch):
    """Replace the ibind client + oauth config so construction is inert."""
    created = {}

    class FakeOAuth1aConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeIbkrClient:
        def __init__(self, account_id=None, use_oauth=False, oauth_config=None):
            self.account_id = account_id
            self.use_oauth = use_oauth
            self.oauth_config = oauth_config
            created["client"] = self

    monkeypatch.setattr(session_mod, "OAuth1aConfig", FakeOAuth1aConfig)
    monkeypatch.setattr(session_mod, "IbkrClient", FakeIbkrClient)
    return created


def _make_trader(fake_client):
    """Build a trader, swap in a FakeClient, and pre-authenticate the session."""
    trader = IBKRTrader(
        "DU123456",
        paper=True,
        consumer_key="ck",
        access_token="at",
        access_token_secret="ats",
        dh_prime="ff",
        signature_key_path="/tmp/sig.pem",
        encryption_key_path="/tmp/enc.pem",
    )
    trader._session.client = fake_client
    trader._session._authenticated = True
    trader._session._session_started_at = 0.0  # not expired path overridden below

    # Make ensure_auth a no-op so individual tests focus on the operation.
    trader._session.ensure_auth = lambda: None
    return trader


# -- construction ------------------------------------------------------------

def test_construct_passes_oauth_config(patched_ibind):
    trader = IBKRTrader(
        "DU999",
        consumer_key="my-ck",
        access_token="my-at",
        access_token_secret="my-ats",
        dh_prime="deadbeef",
        signature_key_path="/keys/sig.pem",
        encryption_key_path="/keys/enc.pem",
    )
    client = patched_ibind["client"]
    assert client.account_id == "DU999"
    assert client.use_oauth is True
    cfg = client.oauth_config.kwargs
    assert cfg["consumer_key"] == "my-ck"
    assert cfg["access_token"] == "my-at"
    assert cfg["access_token_secret"] == "my-ats"
    assert cfg["dh_prime"] == "deadbeef"
    assert cfg["signature_key_fp"] == "/keys/sig.pem"
    assert cfg["encryption_key_fp"] == "/keys/enc.pem"
    assert trader.account_id == "DU999"


# -- contract resolution -----------------------------------------------------

def test_resolve_option_conid_calls_three_secdef_endpoints_in_order():
    fake = FakeClient({
        "iserver/secdef/search": [{"conid": 8314, "symbol": "IBM"}],
        "iserver/secdef/strikes": {"call": [180.0, 185.0, 190.0], "put": [180.0]},
        "iserver/secdef/info": [{"conid": 987654321, "strike": 185.0, "right": "C"}],
    })

    conid = contracts.resolve_option_conid(fake, "IBM", "2026-06-19", 185.0, "call")

    assert conid == 987654321
    paths = [c[1] for c in fake.calls]
    assert paths == [
        "iserver/secdef/search",
        "iserver/secdef/strikes",
        "iserver/secdef/info",
    ]
    # strikes/info requested the resolved underlying conid + JUN26 month + right
    strikes_params = fake.calls[1][2]
    assert strikes_params["conid"] == 8314
    assert strikes_params["month"] == "JUN26"
    info_params = fake.calls[2][2]
    assert info_params["right"] == "C"
    assert info_params["strike"] == 185.0


def test_resolve_option_conid_returns_none_when_strike_missing():
    fake = FakeClient({
        "iserver/secdef/search": [{"conid": 8314}],
        "iserver/secdef/strikes": {"call": [180.0], "put": [180.0]},
    })
    conid = contracts.resolve_option_conid(fake, "IBM", "2026-06-19", 999.0, "call")
    assert conid is None
    # Must not have called secdef/info.
    assert [c[1] for c in fake.calls] == [
        "iserver/secdef/search",
        "iserver/secdef/strikes",
    ]


def test_resolve_option_conid_bad_option_type():
    fake = FakeClient({})
    assert contracts.resolve_option_conid(fake, "IBM", "2026-06-19", 185.0, "banana") is None
    assert fake.calls == []


# -- option order submission -------------------------------------------------

def test_submit_option_order_builds_body_and_confirms(patched_ibind):
    fake = FakeClient({
        "iserver/secdef/search": [{"conid": 8314}],
        "iserver/secdef/strikes": {"call": [185.0], "put": []},
        "iserver/secdef/info": [{"conid": 987654321}],
        # First POST returns a confirmation reply, then a real order id.
        "iserver/account/DU123456/orders": [{"id": "reply-1", "message": ["Are you sure?"]}],
        "iserver/reply/reply-1": [{"order_id": "ORD-42", "order_status": "Submitted"}],
        # (single payloads; no Queue needed since each path is hit once)
    })
    trader = _make_trader(fake)

    out = trader.submit_option_order({
        "chain_symbol": "IBM",
        "option_type": "call",
        "strike": 185.0,
        "expiration": "2026-06-19",
        "side": "BUY",
        "quantity": 2,
        "limit_price": 3.25,
    })

    assert out == {"id": "ORD-42", "symbol": "IBM", "status": "Submitted"}

    # Verify the order body that was POSTed.
    order_post = next(c for c in fake.calls if c[1] == "iserver/account/DU123456/orders")
    body = order_post[2]["orders"][0]
    assert body == {
        "conid": 987654321,
        "side": "BUY",
        "orderType": "LMT",
        "price": 3.25,
        "quantity": 2,
        "tif": "DAY",
    }
    # Verify the reply/confirm step happened with confirmed=True.
    reply_post = next(c for c in fake.calls if c[1] == "iserver/reply/reply-1")
    assert reply_post[2] == {"confirmed": True}


def test_submit_option_order_market_when_no_limit(patched_ibind):
    fake = FakeClient({
        "iserver/secdef/search": [{"conid": 8314}],
        "iserver/secdef/strikes": {"call": [185.0], "put": []},
        "iserver/secdef/info": [{"conid": 111}],
        "iserver/account/DU123456/orders": [{"order_id": "ORD-7", "order_status": "Filled"}],
    })
    trader = _make_trader(fake)

    out = trader.submit_option_order({
        "chain_symbol": "IBM",
        "option_type": "call",
        "strike": 185.0,
        "expiration": "2026-06-19",
        "side": "SELL",
        "quantity": 1,
        "limit_price": None,
    })
    assert out == {"id": "ORD-7", "symbol": "IBM", "status": "Filled"}
    body = next(c for c in fake.calls if c[1] == "iserver/account/DU123456/orders")[2]["orders"][0]
    assert body["orderType"] == "MKT"
    assert "price" not in body


def test_submit_option_order_returns_none_on_unresolvable_conid(patched_ibind):
    fake = FakeClient({
        "iserver/secdef/search": [],  # no underlying -> None
    })
    trader = _make_trader(fake)
    out = trader.submit_option_order({
        "chain_symbol": "ZZZ",
        "option_type": "put",
        "strike": 10.0,
        "expiration": "2026-06-19",
        "side": "BUY",
        "quantity": 1,
        "limit_price": 1.0,
    })
    assert out is None


def test_submit_option_order_returns_none_on_api_error(patched_ibind):
    class ExplodingClient(FakeClient):
        def post(self, path, params=None, **kw):
            if "orders" in path:
                raise RuntimeError("CP 500")
            return super().post(path, params, **kw)

    fake = ExplodingClient({
        "iserver/secdef/search": [{"conid": 8314}],
        "iserver/secdef/strikes": {"call": [185.0], "put": []},
        "iserver/secdef/info": [{"conid": 111}],
    })
    trader = _make_trader(fake)
    out = trader.submit_option_order({
        "chain_symbol": "IBM",
        "option_type": "call",
        "strike": 185.0,
        "expiration": "2026-06-19",
        "side": "BUY",
        "quantity": 1,
        "limit_price": 2.0,
    })
    assert out is None


def test_submit_option_order_rejection_returns_none(patched_ibind):
    fake = FakeClient({
        "iserver/secdef/search": [{"conid": 8314}],
        "iserver/secdef/strikes": {"call": [185.0], "put": []},
        "iserver/secdef/info": [{"conid": 111}],
        "iserver/account/DU123456/orders": [{"id": "r1", "message": ["Order rejected: insufficient funds"]}],
    })
    trader = _make_trader(fake)
    out = trader.submit_option_order({
        "chain_symbol": "IBM",
        "option_type": "call",
        "strike": 185.0,
        "expiration": "2026-06-19",
        "side": "BUY",
        "quantity": 1,
        "limit_price": 2.0,
    })
    assert out is None


# -- equity order submission -------------------------------------------------

def test_submit_order_equity(patched_ibind):
    fake = FakeClient({
        "iserver/account/DU123456/orders": [{"order_id": "E-1", "order_status": "Submitted"}],
    })
    trader = _make_trader(fake)
    out = trader.submit_order({
        "symbol": "AAPL",
        "conid": 265598,
        "side": "BUY",
        "quantity": 10,
        "order_type": "limit",
        "limit_price": 190.5,
    })
    assert out == {"id": "E-1", "symbol": "AAPL", "status": "Submitted"}
    body = fake.calls[0][2]["orders"][0]
    assert body == {
        "conid": 265598,
        "side": "BUY",
        "orderType": "LMT",
        "price": 190.5,
        "quantity": 10,
        "tif": "DAY",
    }


def test_submit_order_missing_conid_returns_none(patched_ibind):
    fake = FakeClient({})
    trader = _make_trader(fake)
    assert trader.submit_order({"symbol": "AAPL", "side": "BUY", "quantity": 1}) is None


# -- positions / orders mapping ----------------------------------------------

def test_account_mapping(patched_ibind):
    fake = FakeClient({
        "portfolio/DU123456/summary": {
            "equitywithloanvalue": {"amount": 100000.0},
            "totalcashvalue": {"amount": 25000.0},
            "buyingpower": {"amount": 50000.0},
            "netliquidation": {"amount": 99000.0},
        },
    })
    trader = _make_trader(fake)
    acct = trader.account()
    assert acct == {
        "equity": 100000.0,
        "cash": 25000.0,
        "buying_power": 50000.0,
        "portfolio_value": 99000.0,
    }


def test_positions_mapping(patched_ibind):
    fake = FakeClient({
        "portfolio/DU123456/positions/0": [
            {
                "contractDesc": "AAPL", "assetClass": "STK", "position": 100,
                "avgCost": 150.0, "mktValue": 16000.0,
            },
            {"assetClass": "OPT", "position": 1, "avgCost": 300.0},  # excluded
            {"contractDesc": "ZERO", "assetClass": "STK", "position": 0},  # zero qty skipped
        ],
    })
    trader = _make_trader(fake)
    pos = trader.positions()
    assert len(pos) == 1
    p = pos[0]
    assert p["symbol"] == "AAPL"
    assert p["qty"] == 100.0
    assert p["side"] == "long"
    assert p["market_value"] == 16000.0
    assert p["avg_entry"] == 150.0
    assert p["unrealized_pl"] == 1000.0  # 16000 - 15000
    assert p["unrealized_pl_pct"] == round(1000.0 / 15000.0, 4)


def test_options_positions_mapping(patched_ibind):
    fake = FakeClient({
        "portfolio/DU123456/positions/0": [
            {
                "ticker": "IBM", "assetClass": "OPT", "position": 2,
                "avgCost": 300.0, "mktPrice": 3.5, "mktValue": 700.0,
                "strike": 185.0, "expiry": "20260619", "putOrCall": "C",
                "multiplier": 100, "undPrice": 188.0,
            },
            {"ticker": "AAPL", "assetClass": "STK", "position": 10},  # excluded
        ],
    })
    trader = _make_trader(fake)
    opts = trader.options_positions()
    assert len(opts) == 1
    o = opts[0]
    assert o["chain_symbol"] == "IBM"
    assert o["option_type"] == "call"
    assert o["position_type"] == "long"
    assert o["strike"] == 185.0
    assert o["expiration"] == "2026-06-19"
    assert o["quantity"] == 2.0
    assert o["avg_price"] == 3.0  # 300 / 100
    assert o["mark_price"] == 3.5
    assert o["multiplier"] == 100.0
    assert o["cost_basis"] == 600.0  # 2 * 3.0 * 100
    assert o["current_value"] == 700.0
    assert o["unrealized_pl"] == 100.0
    assert o["underlying_price"] == 188.0
    assert set(o["greeks"].keys()) == {"delta", "gamma", "theta", "vega", "iv"}


def test_open_orders_mapping(patched_ibind):
    fake = FakeClient({
        "iserver/account/orders": {
            "orders": [
                {
                    "orderId": 111, "ticker": "AAPL", "side": "BUY",
                    "totalSize": 10, "orderType": "LMT", "price": 190.0,
                    "status": "Submitted", "secType": "STK",
                },
                {
                    "orderId": 222, "ticker": "MSFT", "side": "SELL",
                    "totalSize": 5, "orderType": "MKT", "status": "Filled",
                    "secType": "STK",
                },  # not open -> excluded
                {
                    "orderId": 333, "ticker": "IBM", "side": "BUY",
                    "totalSize": 1, "status": "Submitted", "secType": "OPT",
                },  # option -> excluded from equity view
            ]
        },
    })
    trader = _make_trader(fake)
    oo = trader.open_orders()
    assert len(oo) == 1
    o = oo[0]
    assert o["id"] == "111"
    assert o["symbol"] == "AAPL"
    assert o["side"] == "BUY"
    assert o["qty"] == 10.0
    assert o["type"] == "lmt"
    assert o["limit_price"] == 190.0
    assert o["status"] == "Submitted"


def test_options_orders_mapping(patched_ibind):
    fake = FakeClient({
        "iserver/account/orders": {
            "orders": [
                {
                    "orderId": 333, "ticker": "IBM", "side": "SELL",
                    "totalSize": 2, "orderType": "LMT", "price": 3.5,
                    "status": "Submitted", "secType": "OPT",
                    "strike": 185.0, "expiry": "20260619", "right": "P",
                    "timeInForce": "DAY",
                },
                {"orderId": 444, "secType": "STK", "status": "Submitted"},  # excluded
            ]
        },
    })
    trader = _make_trader(fake)
    oo = trader.options_orders()
    assert len(oo) == 1
    o = oo[0]
    assert o["order_id"] == "333"
    assert o["state"] == "Submitted"
    assert o["quantity"] == 2.0
    assert o["price"] == 3.5
    assert o["direction"] == "credit"  # SELL
    assert o["time_in_force"] == "DAY"
    assert len(o["legs"]) == 1
    leg = o["legs"][0]
    assert leg["side"] == "SELL"
    assert leg["strike"] == 185.0
    assert leg["expiration"] == "20260619"
    assert leg["option_type"] == "put"
    assert leg["chain_symbol"] == "IBM"


# -- cancel ------------------------------------------------------------------

def test_cancel_order(patched_ibind):
    fake = FakeClient({})
    trader = _make_trader(fake)
    trader.cancel_order("ORD-9")
    assert fake.calls == [("DELETE", "iserver/account/DU123456/order/ORD-9", None)]


def test_cancel_all(patched_ibind):
    fake = FakeClient({
        "iserver/account/orders": {
            "orders": [
                {"orderId": 1, "secType": "STK", "status": "Submitted"},
                {"orderId": 2, "secType": "STK", "status": "Submitted"},
            ]
        },
    })
    trader = _make_trader(fake)
    trader.cancel_all()
    deletes = [c for c in fake.calls if c[0] == "DELETE"]
    assert len(deletes) == 2


# -- auth status -------------------------------------------------------------

def test_auth_status(patched_ibind):
    fake = FakeClient({
        "iserver/auth/status": {"authenticated": True},
    })
    trader = _make_trader(fake)
    trader._session._session_started_at = 1000.0
    status = trader.auth_status()
    assert status["authenticated"] is True
    assert status["account_id"] == "DU123456"
    assert status["session_expires_at"] == 1000.0 + 23 * 60 * 60
