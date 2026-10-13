"""Offline tests for scripts/stop_sweeper.py — guardrails + queue semantics.

No network: a FakeClient stands in for the auth-service. The point of the
guard tests is that NOTHING outside a percentage trailing-stop SELL order can
leave this tool, and MCP traffic is read-only.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app import stop_sweeper as sw
from app.stop_sweeper import (
    GuardrailViolation,
    StopStore,
    build_option_take_profit_order,
    build_payload,
    check,
    option_take_profit_limit,
    renew,
    sweep,
    sweep_options_take_profit,
    validate_mcp_call,
    validate_trailing_stop_payload,
)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #

class FakeClient:
    """In-memory stand-in for the auth-service; validates like real clients."""

    def __init__(self, book=None, option_positions=None, option_orders=None):
        self.book = book or []          # orders "on RH"
        self.rh_reads = 0               # how often we hit "RH"
        self.placed = []
        self.replaced = []
        self._option_positions = option_positions if option_positions is not None else []
        self._option_orders = option_orders if option_orders is not None else []

    def get_stops(self):
        self.rh_reads += 1
        return list(self.book)

    def place_stop(self, payload, dry_run=True):
        validate_trailing_stop_payload(payload, live=not dry_run)
        self.placed.append((payload, dry_run))
        return {"dry_run": dry_run, "payload": payload}

    def replace_stop(self, order_id, payload, dry_run=True):
        validate_trailing_stop_payload(payload, live=not dry_run)
        if not order_id:
            raise GuardrailViolation("replace requires an existing order_id")
        self.replaced.append((order_id, payload, dry_run))
        return {"dry_run": dry_run, "order_id": order_id}

    def mcp_call(self, payload):
        validate_mcp_call(payload)
        return {"ok": True}

    def get_option_positions(self):
        return list(self._option_positions)

    def get_option_orders(self):
        return list(self._option_orders)

    def place_option_limit(self, order, dry_run=True):
        self.placed.append((order, dry_run))
        return {"dry_run": dry_run, "id": "opt-tp-1", "state": "queued"}


def rh_order(symbol, side="sell", pct="16", order_id=None, created=None):
    return {
        "id": order_id or str(uuid.uuid4()),
        "symbol": symbol,
        "state": "confirmed",
        "side": side,
        "quantity": "5",
        "trigger": "stop",
        "created_at": created or datetime.now(timezone.utc).isoformat(),
        "account": "https://api.robinhood.com/accounts/X/",
        "instrument": "https://api.robinhood.com/instruments/Y/",
        "trailing_peg": {"type": "percentage", "percentage": pct},
    }


@pytest.fixture
def store(tmp_path):
    return StopStore(str(tmp_path / "stops.sqlite3"))


# --------------------------------------------------------------------------- #
# payload guardrails — destructive orders must not get out
# --------------------------------------------------------------------------- #

def valid_payload(**over):
    p = build_payload("AAPL", "sell", 1, 16)
    p.update(over)
    return p


def test_built_payload_passes_guard():
    validate_trailing_stop_payload(valid_payload())


@pytest.mark.parametrize("mutation,why", [
    ({"side": "buy"}, "buy order"),
    ({"type": "limit"}, "limit order"),
    ({"trailing_peg": None}, "missing trailing peg"),
    ({"price": "250.00"}, "smuggled limit price key"),
])
def test_guard_rejects_destructive_payloads(mutation, why):
    with pytest.raises(GuardrailViolation):
        validate_trailing_stop_payload(valid_payload(**mutation)), why


def test_live_placement_requires_account_and_instrument():
    # dry-run tolerates empty URLs, live must not
    validate_trailing_stop_payload(valid_payload(), live=False)
    with pytest.raises(GuardrailViolation):
        validate_trailing_stop_payload(valid_payload(), live=True)
    validate_trailing_stop_payload(
        valid_payload(account="https://a/", instrument="https://i/"), live=True)


def test_replace_requires_order_id():
    c = FakeClient()
    with pytest.raises(GuardrailViolation):
        c.replace_stop("", valid_payload())


# --------------------------------------------------------------------------- #
# MCP guardrails — read-only surface
# --------------------------------------------------------------------------- #

def mcp(method, tool=None):
    p = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}
    if tool:
        p["params"] = {"name": tool, "arguments": {}}
    return p


def test_mcp_allows_readonly():
    for payload in [mcp("initialize"), mcp("tools/list"),
                    mcp("tools/call", "get_positions"),
                    mcp("tools/call", "get_quote")]:
        validate_mcp_call(payload)


@pytest.mark.parametrize("payload", [
    mcp("tools/call", "place_order"),
    mcp("tools/call", "cancel_order"),
    mcp("resources/read"),
])
def test_mcp_blocks_destructive_or_unknown(payload):
    with pytest.raises(GuardrailViolation):
        validate_mcp_call(payload)


# --------------------------------------------------------------------------- #
# sweep + queue semantics
# --------------------------------------------------------------------------- #

def test_sweep_places_only_for_uncovered(store):
    c = FakeClient(book=[rh_order("IWN")])
    out = sweep(c, store, ["AAPL", "IWN"])
    assert out["placed"] == []
    assert [p["symbol"] for p in out["simulated"]] == ["AAPL"]
    assert all(dry for _, dry in c.placed), "sweep must default to dry_run"
    assert store.get("IWN")["state"] == "confirmed"
    assert store.get("AAPL") is None


def test_sweep_applies_equity_stop_mapping(store):
    c = FakeClient()

    out = sweep(c, store, ["AAPL", "MU", "NBIS"], trail_percent=12)

    percentages = {
        payload["symbol"]: float(payload["trailing_peg"]["percentage"])
        for payload, _ in c.placed
    }
    assert percentages == {"AAPL": 12.0, "MU": 4.0, "NBIS": 6.0}
    assert {row["symbol"] for row in out["simulated"]} == {
        "AAPL", "MU", "NBIS",
    }


def test_sweep_resolves_symbol_missing_from_robinhood_order(store):
    order = rh_order("")
    order.pop("symbol")
    c = FakeClient(book=[order])

    out = sweep(
        c,
        store,
        ["IWN"],
        order_symbol_resolver=lambda _: "IWN",
    )

    assert out["active_from_rh"] == 1
    assert out["placed"] == []
    assert out["simulated"] == []
    assert c.placed == []
    assert store.get("IWN")["order_id"] == order["id"]


def test_sweep_aborts_when_active_order_symbol_cannot_be_resolved(store):
    order = rh_order("")
    order.pop("symbol")
    c = FakeClient(book=[order])

    out = sweep(
        c,
        store,
        ["IWN"],
        order_symbol_resolver=lambda _: "",
    )

    assert out["unresolved_active_orders"] == [order["id"]]
    assert out["placed"] == []
    assert out["simulated"] == []
    assert c.placed == []
    assert not store.swept_today(), "an incomplete RH book must retry next tick"


def test_check_is_sqlite_first_no_rh_call(store):
    c = FakeClient(book=[rh_order("IWN")])
    sweep(c, store, ["IWN"])
    reads_after_sweep = c.rh_reads
    row = check(c, store, "IWN")
    assert row["symbol"] == "IWN"
    assert not row["expiring_soon"]
    assert c.rh_reads == reads_after_sweep, "check must not hit RH when fresh"


def test_expiring_stop_triggers_explicit_rh_check_and_renew(store):
    old = rh_order("IWN", created="2026-04-10T00:00:00+00:00")  # ~86d ago
    c = FakeClient(book=[old])
    sweep(c, store, ["IWN"])          # sweep itself renews the expiring stop
    reads = c.rh_reads
    row = check(c, store, "IWN")      # dry-run didn't change RH -> renews again
    assert row["expiring_soon"]
    assert c.rh_reads > reads, "near-expiry must force an RH re-read"
    assert {r[0] for r in c.replaced} == {old["id"]}
    assert all(r[2] is True for r in c.replaced), "renew must default to dry_run"


def test_renew_skips_buy_side_stop_instead_of_crashing(store):
    # a buy-side stop in the book must never be replayed by our sell-only tool
    weird = rh_order("SHORTY", side="buy", created="2026-04-10T00:00:00+00:00")
    c = FakeClient(book=[weird])
    sweep(c, store, ["SHORTY"])
    out = renew(c, store, "SHORTY")
    assert out["action"] == "renew_skipped"
    assert c.replaced == []


def _live_kwargs(qty=5, price=100.0):
    return dict(dry_run=False, trail_percent=12, qty_map={"AAPL": qty},
                price_map={"AAPL": price},
                account_url="https://api.robinhood.com/accounts/X/",
                instrument_resolver=lambda s: f"https://api.robinhood.com/instruments/{s}/")


class OrderIdClient(FakeClient):
    """Box that returns a real RH-shaped order id (successful placement)."""
    def place_stop(self, payload, dry_run=True):
        validate_trailing_stop_payload(payload, live=not dry_run)
        self.placed.append((payload, dry_run))
        return {"id": "ord-123", "state": "queued", **payload}


def test_live_sweep_places_whole_shares_with_stop_price(store):
    c = OrderIdClient()
    out = sweep(c, store, ["AAPL"], **_live_kwargs(qty=42.9, price=100.0))
    assert [p["symbol"] for p in out["placed"]] == ["AAPL"]
    payload, dry = c.placed[0]
    assert dry is False
    assert payload["quantity"] == "42"                # floored to whole shares
    assert payload["stop_price"] == "88.0"            # 12% below 100
    assert store.get("AAPL")["order_id"] == "ord-123"


def test_live_sweep_uses_instrument_map_when_public_lookup_fails(store):
    """TSPH is not on the public RH instruments API; use the position URL."""
    c = OrderIdClient()
    out = sweep(
        c, store, ["TSPH"],
        dry_run=False, trail_percent=12,
        qty_map={"TSPH": 20}, price_map={"TSPH": 10.0},
        account_url="https://api.robinhood.com/accounts/X/",
        instrument_resolver=lambda _: "",
        instrument_map={"TSPH": "https://api.robinhood.com/instruments/tsph/"},
    )
    assert [p["symbol"] for p in out["placed"]] == ["TSPH"]
    assert c.placed[0][0]["instrument"] == "https://api.robinhood.com/instruments/tsph/"
    assert store.swept_today()


def test_live_sweep_does_not_latch_when_instrument_unresolved(store):
    c = OrderIdClient()
    out = sweep(
        c, store, ["TSPH"],
        dry_run=False, trail_percent=12,
        qty_map={"TSPH": 20}, price_map={"TSPH": 10.0},
        account_url="https://api.robinhood.com/accounts/X/",
        instrument_resolver=lambda _: "",
    )
    assert out["placed"] == []
    assert out["skipped"] == [{"symbol": "TSPH", "reason": "unresolved_instrument"}]
    assert not store.swept_today(), "retryable skips must not latch the day"


def test_renew_prefers_known_order_id_over_earlier_symbol_match(store):
    decoy = rh_order("TSPH")
    target = rh_order("")
    target.pop("symbol")
    c = FakeClient(book=[decoy, target])
    out = renew(
        c, store, "TSPH",
        known_order={"id": target["id"], "symbol": "TSPH"},
        order_symbol_resolver=lambda _: "",
    )
    assert out["action"] == "renewed"
    assert c.replaced[0][0] == target["id"]


def test_live_sweep_skips_account_before_instrument_lookup(store):
    calls = []

    def resolver(symbol):
        calls.append(symbol)
        raise AssertionError("public instrument lookup must not run")

    out = sweep(
        OrderIdClient(), store, ["AAPL"],
        dry_run=False, trail_percent=12,
        qty_map={"AAPL": 5}, price_map={"AAPL": 100.0},
        account_url="",
        instrument_resolver=resolver,
    )
    assert out["skipped"] == [{"symbol": "AAPL", "reason": "unresolved_account"}]
    assert calls == []


def test_renew_matches_known_order_id_when_symbol_is_missing(store):
    order = rh_order("")
    order.pop("symbol")
    c = FakeClient(book=[order])
    out = renew(
        c, store, "TSPH",
        known_order={"id": order["id"], "symbol": "TSPH", **order},
        order_symbol_resolver=lambda _: "",
    )
    assert out["action"] == "renewed"
    assert c.replaced[0][0] == order["id"]


def test_sweep_does_not_latch_when_renew_cannot_confirm_order(store):
    old = rh_order("IWN", created="2026-04-10T00:00:00+00:00")

    class DropClient(FakeClient):
        def get_stops(self):
            self.rh_reads += 1
            return [old] if self.rh_reads == 1 else []

    out = sweep(DropClient(), store, ["IWN"])
    assert out["renewed"][0]["action"] == "missing_in_rh"
    assert not store.swept_today()


def test_rh_rejection_without_id_is_not_placed(store):
    # box returns 200 but RH rejected (no id) -> must count as skipped
    class RejectClient(FakeClient):
        def place_stop(self, payload, dry_run=True):
            validate_trailing_stop_payload(payload, live=not dry_run)
            self.placed.append((payload, dry_run))
            return {"non_field_errors": ["Stop limit order requested, "
                                         "but no stop price provided."]}
    c = RejectClient()
    out = sweep(c, store, ["AAPL"], **_live_kwargs(qty=5))
    assert out["placed"] == []
    assert out["skipped"][0]["reason"].startswith("rh_rejected")
    assert store.get("AAPL") is None


def test_prune_removes_rows_gone_from_rh(store):
    c = FakeClient(book=[rh_order("AAPL"), rh_order("IWN")])
    sweep(c, store, ["AAPL", "IWN"])
    c.book = [rh_order("AAPL")]              # IWN vanished from RH
    out = sweep(c, store, ["AAPL"])
    assert out["pruned"] == ["IWN"]
    assert store.get("IWN") is None


# --------------------------------------------------------------------------- #
# options take-profit sweep
# --------------------------------------------------------------------------- #

def _long_option_pos(**overrides):
    base = {
        "chain_symbol": "MU",
        "option_type": "call",
        "strike": 95.0,
        "expiration": "2026-03-20",
        "position_type": "long",
        "quantity": 2,
        "purchase_price": 2.0,
        "average_price": 200.0,
        "entry_source": "position_average_price",
        "source": "robinhood",
    }
    base.update(overrides)
    return base


def test_option_take_profit_limit_price():
    assert option_take_profit_limit(2.0, 50) == 3.0


def test_sweep_options_take_profit_places_sell_to_close_limit(store):
    pos = [_long_option_pos()]
    client = FakeClient(option_positions=pos)
    out = sweep_options_take_profit(client, store, tp_percent=50, dry_run=True)
    assert len(out["placed"]) == 1
    order, dry = client.placed[0]
    assert dry is True
    assert order["action"] == "sell_to_close"
    assert order["limit_price"] == "3.0"
    assert order["contract"]["chain_symbol"] == "MU"
    assert store.option_tp_swept_today()


def test_sweep_options_take_profit_runs_once_per_day(store):
    pos = [_long_option_pos()]
    client = FakeClient(option_positions=pos)
    sweep_options_take_profit(client, store, dry_run=True)
    client.placed.clear()
    out = sweep_options_take_profit(client, store, dry_run=True)
    assert out.get("skipped") == "already_swept_today"
    assert client.placed == []


def test_sweep_options_take_profit_skips_duplicate_open_order(store):
    pos = [_long_option_pos()]
    open_orders = [{
        "state": "queued",
        "price": 3.0,
        "legs": [{
            "side": "sell",
            "chain_symbol": "MU",
            "option_type": "call",
            "strike": 95.0,
            "expiration": "2026-03-20",
        }],
    }]
    client = FakeClient(option_positions=pos, option_orders=open_orders)
    out = sweep_options_take_profit(client, store, tp_percent=50, dry_run=True)
    assert out["placed"] == []
    assert any(s.get("reason") == "tp_limit_already_open" for s in out["skipped"])
