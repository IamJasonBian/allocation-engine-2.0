"""Offline tests for scripts/stop_sweeper.py — guardrails + queue semantics.

No network: a FakeClient stands in for the auth-service. The point of the
guard tests is that NOTHING outside a percentage trailing-stop SELL order can
leave this tool, and MCP traffic is read-only.
"""

import uuid

import pytest

from app import stop_sweeper as sw
from app.stop_sweeper import (
    GuardrailViolation,
    StopStore,
    build_payload,
    check,
    renew,
    sweep,
    validate_mcp_call,
    validate_trailing_stop_payload,
)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #

class FakeClient:
    """In-memory stand-in for the auth-service; validates like real clients."""

    def __init__(self, book=None):
        self.book = book or []          # orders "on RH"
        self.rh_reads = 0               # how often we hit "RH"
        self.placed = []
        self.replaced = []

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


def rh_order(symbol, side="sell", pct="16", order_id=None, created=None):
    return {
        "id": order_id or str(uuid.uuid4()),
        "symbol": symbol,
        "state": "confirmed",
        "side": side,
        "quantity": "5",
        "trigger": "stop",
        "created_at": created or "2026-07-01T00:00:00+00:00",
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
    ({"trigger": "immediate"}, "plain market sell (no stop trigger)"),
    ({"type": "limit"}, "limit order"),
    ({"time_in_force": "gfd"}, "non-GTC"),
    ({"trailing_peg": None}, "missing trailing peg"),
    ({"trailing_peg": {"type": "price", "price": "5"}}, "price peg"),
    ({"trailing_peg": {"type": "percentage", "percentage": "0"}}, "0% trail"),
    ({"trailing_peg": {"type": "percentage", "percentage": "80"}}, ">50% trail"),
    ({"trailing_peg": {"type": "percentage", "percentage": "nope"}}, "NaN trail"),
    ({"quantity": "0"}, "zero quantity"),
    ({"quantity": "-3"}, "negative quantity"),
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


def test_fake_client_enforces_guard_on_place():
    c = FakeClient()
    with pytest.raises(GuardrailViolation):
        c.place_stop(valid_payload(side="buy"))
    assert c.placed == []


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
    mcp("tools/call", "buy"),
    mcp("tools/call", "sell_shares"),
    mcp("tools/call", "cancel_order"),
    mcp("tools/call", ""),
    mcp("resources/read"),
    mcp("completion/complete"),
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
    assert [p["symbol"] for p in out["placed"]] == ["AAPL"]
    assert all(dry for _, dry in c.placed), "sweep must default to dry_run"
    assert store.get("IWN")["state"] == "confirmed"
    assert store.get("AAPL")["state"] == "dry_run"


def test_check_is_sqlite_first_no_rh_call(store):
    c = FakeClient(book=[rh_order("IWN")])
    sweep(c, store, ["IWN"])
    reads_after_sweep = c.rh_reads
    row = check(c, store, "IWN")
    assert row["symbol"] == "IWN"
    assert not row["expiring_soon"]
    assert c.rh_reads == reads_after_sweep, "check must not hit RH when fresh"


def test_check_repopulates_when_db_empty(store):
    c = FakeClient(book=[rh_order("IWN")])
    row = check(c, store, "IWN")            # empty db -> sweep -> row
    assert row is not None
    assert c.rh_reads >= 1


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


def test_live_renew_refreshes_expiry_so_check_stops_renewing(store):
    old = rh_order("IWN", created="2026-04-10T00:00:00+00:00")
    c = FakeClient(book=[old])
    sweep(c, store, ["IWN"], dry_run=False)   # live sweep renews for real

    # simulate RH returning the replacement order with a fresh created_at
    class LiveClient(FakeClient):
        def replace_stop(self, order_id, payload, dry_run=True):
            super().replace_stop(order_id, payload, dry_run)
            return rh_order("IWN", created=sw._now_iso())

    c2 = LiveClient(book=[old])
    renew(c2, store, "IWN", dry_run=False)
    row = store.get("IWN")
    assert not sw._expiring_soon(row), "live renew must advance expires_at"
    replaced_before = len(c2.replaced)
    check(c2, store, "IWN")                    # fresh row -> no more renews
    assert len(c2.replaced) == replaced_before


def test_renew_skips_buy_side_stop_instead_of_crashing(store):
    # a buy-side stop in the book must never be replayed by our sell-only tool
    weird = rh_order("SHORTY", side="buy", created="2026-04-10T00:00:00+00:00")
    c = FakeClient(book=[weird])
    sweep(c, store, ["SHORTY"])
    out = renew(c, store, "SHORTY")
    assert out["action"] == "renew_skipped"
    assert c.replaced == []


def test_live_sweep_places_with_real_qty_and_urls(store):
    c = FakeClient()
    out = sweep(c, store, ["AAPL"], dry_run=False,
                qty_map={"AAPL": 42.5},
                account_url="https://api.robinhood.com/accounts/X/",
                instrument_resolver=lambda s: f"https://api.robinhood.com/instruments/{s}/")
    assert [p["symbol"] for p in out["placed"]] == ["AAPL"]
    payload, dry = c.placed[0]
    assert dry is False
    assert payload["quantity"] == "42.5"
    assert payload["account"] and payload["instrument"]
    assert store.get("AAPL")["state"] == "submitted"


def test_live_sweep_skips_without_quantity(store):
    c = FakeClient()
    out = sweep(c, store, ["AAPL"], dry_run=False, qty_map={},
                account_url="https://a/", instrument_resolver=lambda s: "https://i/")
    assert c.placed == []
    assert out["skipped"] == [{"symbol": "AAPL", "reason": "no_quantity"}]


def test_live_sweep_skips_unresolved_instrument(store):
    c = FakeClient()
    out = sweep(c, store, ["AAPL"], dry_run=False, qty_map={"AAPL": 5},
                account_url="https://a/", instrument_resolver=lambda s: "")
    assert c.placed == []
    assert out["skipped"][0]["reason"] == "unresolved_urls"


def test_dry_run_sweep_unchanged_without_urls(store):
    c = FakeClient()
    out = sweep(c, store, ["AAPL"], dry_run=True)
    assert [p["symbol"] for p in out["placed"]] == ["AAPL"]
    assert c.placed[0][1] is True


def test_prune_removes_rows_gone_from_rh(store):
    c = FakeClient(book=[rh_order("AAPL"), rh_order("IWN")])
    sweep(c, store, ["AAPL", "IWN"])
    c.book = [rh_order("AAPL")]              # IWN vanished from RH
    out = sweep(c, store, ["AAPL"])
    assert out["pruned"] == ["IWN"]
    assert store.get("IWN") is None
