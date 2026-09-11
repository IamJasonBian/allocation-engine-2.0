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
# vol-scaled trail percentages (compute_trail_percents)
# --------------------------------------------------------------------------- #

def _weighted_avg(mvs, trails):
    total = sum(mvs.values())
    return sum(mvs[s] / total * trails[s] for s in mvs)


def test_trail_invariant_holds_exactly_without_quantize():
    mvs = {"AAPL": 5000, "TSLA": 3000, "KO": 2000}
    sigmas = {"AAPL": 0.25, "TSLA": 0.45, "KO": 0.12}
    trails = sw.compute_trail_percents(mvs, sigmas, quantum=0)
    assert abs(_weighted_avg(mvs, trails) - sw.TRAIL_PERCENT) < 1e-9
    assert trails["TSLA"] > trails["AAPL"] > trails["KO"]


def test_trail_clamps_and_renormalizes_within_tolerance():
    mvs = {"MEME": 1000, "BOND": 9000}
    sigmas = {"MEME": 2.0, "BOND": 0.05}   # extreme spread forces both clamps
    trails = sw.compute_trail_percents(mvs, sigmas, quantum=0)
    assert all(sw.TRAIL_FLOOR <= t <= sw.TRAIL_CAP for t in trails.values())
    assert abs(_weighted_avg(mvs, trails) - sw.TRAIL_PERCENT) <= 0.1


def test_trail_degenerate_sigma_falls_back_flat():
    mvs = {"AAPL": 5000, "NOSIG": 5000, "ZEROSIG": 1000}
    sigmas = {"AAPL": 0.3, "ZEROSIG": 0.0}
    trails = sw.compute_trail_percents(mvs, sigmas, quantum=0)
    assert trails["NOSIG"] == sw.TRAIL_PERCENT
    assert trails["ZEROSIG"] == sw.TRAIL_PERCENT
    # AAPL is the whole scaled pool -> its own weighted mean -> flat too
    assert abs(trails["AAPL"] - sw.TRAIL_PERCENT) < 1e-9


def test_trail_empty_sigmas_is_flat_16_compatible():
    mvs = {"AAPL": 5000, "IWN": 3000}
    trails = sw.compute_trail_percents(mvs, {})
    assert trails == {"AAPL": sw.TRAIL_PERCENT, "IWN": sw.TRAIL_PERCENT}


def test_sweep_uses_trail_map_per_symbol(store):
    c = FakeClient()
    sweep(c, store, ["AAPL", "IWN"],
          trail_map={"AAPL": 12.5, "IWN": 20.0})
    pegs = {p["symbol"]: float(p["trailing_peg"]["percentage"])
            for p, _ in c.placed}
    assert pegs == {"AAPL": 12.5, "IWN": 20.0}


# --------------------------------------------------------------------------- #
# timezone-safe expiry checks (_parse_utc / _plus_days / _expiring_soon)
# --------------------------------------------------------------------------- #

def test_parse_utc_coerces_naive_to_utc():
    from datetime import timezone
    dt = sw._parse_utc("2026-08-06T00:00:15")      # no offset -> assume UTC
    assert dt is not None and dt.tzinfo == timezone.utc


def test_expiring_soon_with_naive_created_does_not_raise():
    # Regression: a naive expires_at used to raise TypeError (naive vs aware).
    naive_created = "2026-08-06T00:00:15"          # no tz
    expires = sw._plus_days(naive_created, sw.GTC_LIFETIME_DAYS)
    assert "+00:00" in expires                      # _plus_days now emits aware
    assert sw._expiring_soon({"expires_at": expires}) is False


# --------------------------------------------------------------------------- #
# high-water stop levels: RH client read + apply (TRAILING_STOP_HIGH_WATER.md)
# --------------------------------------------------------------------------- #

def stopped_order(symbol, level, pct="16", **over):
    o = rh_order(symbol, pct=pct)
    o["stop_price"] = str(level)
    o.update(over)
    return o


def test_stop_level_read_from_order():
    assert sw.stop_level_of(stopped_order("AAPL", "88.10")) == (88.10, "stop_price")


def test_stop_level_probes_alternate_fields():
    o = rh_order("AAPL")
    o["trailing_peg"]["price"] = "91.25"
    assert sw.stop_level_of(o) == (91.25, "trailing_peg.price")


def test_stop_level_absent_is_not_guessed_from_peg():
    # An order with only a peg percentage carries no level: the read must say
    # so rather than inventing one, or a guess establishes a floor.
    assert sw.stop_level_of(rh_order("AAPL")) == (None, "")


@pytest.mark.parametrize("bad", ["", None, "0", "not-a-number"])
def test_stop_level_rejects_unusable_values(bad):
    assert sw.stop_level_of({"stop_price": bad}) == (None, "")


def test_read_stop_levels_keys_by_symbol_with_peg():
    out = sw.read_stop_levels([stopped_order("AAPL", "88.1"), rh_order("IWN", pct="20")])
    assert out["AAPL"]["level"] == 88.1
    assert out["AAPL"]["source"] == "stop_price"
    assert out["IWN"]["level"] is None
    assert out["IWN"]["trail_percent"] == 20.0


def test_apply_high_water_is_flat_percent_without_a_mark():
    plan = sw.apply_high_water(100, 16, None)
    assert (plan["stop_price"], plan["peg_percent"]) == (84.0, 16.0)
    assert not plan["binding"] and not plan["breach"]


def test_apply_high_water_floor_binds_and_never_sits_below_the_mark():
    # Peaked at 100 (stop 88), price has since drifted to 90: the flat 16%
    # would write 75.60 — well under the level already earned.
    plan = sw.apply_high_water(90, 16, 88.0)
    assert plan["binding"] and not plan["breach"]
    assert plan["stop_price"] >= 88.0
    assert plan["peg_percent"] < 16


def test_apply_high_water_quantizes_down_so_the_stop_stays_above_the_mark():
    # Rounding the peg up would drop the stop under the mark it floors.
    for price, hw in [(90, 88.0), (101.37, 93.11), (55.5, 51.02)]:
        plan = sw.apply_high_water(price, 16, hw)
        assert plan["stop_price"] >= hw
        assert plan["peg_percent"] % sw.TRAIL_QUANTUM == 0


def test_apply_high_water_never_rounds_the_stop_below_the_mark():
    # Cent-rounding must not shave the level the mark is there to hold.
    for price, hw in [(100.004, 88.0035), (33.337, 31.1119), (7.771, 7.7099)]:
        plan = sw.apply_high_water(price, 16, hw)
        assert plan["breach"] or plan["stop_price"] >= hw


def test_apply_high_water_flags_a_tight_peg_rather_than_loosening_it():
    # Below TRAIL_FLOOR, but honouring the floor would lower an earned level.
    plan = sw.apply_high_water(90, 16, 88.0)
    assert plan["tight"] and plan["peg_percent"] < sw.TRAIL_FLOOR
    assert plan["stop_price"] >= 88.0


def test_apply_high_water_breach_yields_no_placeable_order():
    plan = sw.apply_high_water(80, 16, 88.0)
    assert plan["breach"]
    assert plan["stop_price"] is None and plan["peg_percent"] is None


def test_apply_high_water_within_one_quantum_is_a_breach_not_a_rounding():
    plan = sw.apply_high_water(100, 16, 99.8)     # implies a 0.2% peg
    assert plan["breach"]


# --------------------------------------------------------------------------- #
# high-water store: monotone, survives pruning, resets keep their reason
# --------------------------------------------------------------------------- #

def test_hw_observe_raises_but_never_lowers(store):
    assert store.hw_observe("AAPL", 88.0) == (88.0, True)
    assert store.hw_observe("AAPL", 91.5) == (91.5, True)
    assert store.hw_observe("AAPL", 70.0) == (91.5, False)
    assert store.hw_get("AAPL")["hw_stop"] == 91.5


@pytest.mark.parametrize("bad", [None, 0, -5, "junk"])
def test_hw_observe_ignores_unusable_levels(store, bad):
    store.hw_observe("AAPL", 88.0)
    assert store.hw_observe("AAPL", bad) == (88.0, False)


def test_hw_mark_survives_prune_of_the_stops_row(store):
    # The mark has to outlive the order it was read from: prune_missing drops
    # the stops row when a symbol leaves the RH book.
    c = FakeClient(book=[rh_order("AAPL"), rh_order("IWN")])
    sweep(c, store, ["AAPL", "IWN"])
    store.hw_observe("IWN", 42.0)
    c.book = [rh_order("AAPL")]
    sweep(c, store, ["AAPL"])
    assert store.get("IWN") is None
    assert store.hw_get("IWN")["hw_stop"] == 42.0


def test_hw_reset_clears_the_mark_and_keeps_the_reason(store):
    store.hw_observe("AAPL", 88.0)
    row = store.hw_reset("AAPL", "filled")
    assert row["hw_stop"] is None
    assert row["reset_reason"] == "filled" and row["reset_at"]
    # A reset mark is re-established by the next observation, not blocked.
    assert store.hw_observe("AAPL", 51.0) == (51.0, True)


# --------------------------------------------------------------------------- #
# reconciliation engine
# --------------------------------------------------------------------------- #

def test_reconcile_seeds_marks_from_the_rh_book(store):
    c = FakeClient(book=[stopped_order("AAPL", "88.10"), stopped_order("IWN", "40.00")])
    out = sw.reconcile(c, store)
    assert out["checked"] == 2
    assert out["source_counts"] == {"stop_price": 2}
    assert store.hw_get("AAPL")["hw_stop"] == 88.10
    assert out["findings"] == []


def test_reconcile_never_touches_the_rh_book(store):
    c = FakeClient(book=[stopped_order("AAPL", "88.10")])
    sw.reconcile(c, store, price_map={"AAPL": 60}, qty_map={"AAPL": 5})
    assert c.placed == [] and c.replaced == []


def test_reconcile_reports_a_level_regression(store):
    c = FakeClient(book=[stopped_order("AAPL", "88.00")])
    sw.reconcile(c, store)
    # GTC renewal rewrote the order against a lower mark.
    c.book = [stopped_order("AAPL", "70.00")]
    out = sw.reconcile(c, store)
    reg = [f for f in out["findings"] if f["finding"] == "level_regression"]
    assert len(reg) == 1
    assert reg[0]["give_back"] == 18.0 and reg[0]["verified"] is True
    assert store.hw_get("AAPL")["hw_stop"] == 88.00      # mark did not follow


def test_reconcile_marks_derived_findings_unverified(store):
    # No level on the order: derived from the mark, so a regression against it
    # is weaker evidence and must say so.
    c = FakeClient(book=[rh_order("AAPL")])
    sw.reconcile(c, store, price_map={"AAPL": 100})       # -> 84.00
    out = sw.reconcile(c, store, price_map={"AAPL": 90})  # -> 75.60
    reg = [f for f in out["findings"] if f["finding"] == "level_regression"]
    assert len(reg) == 1 and reg[0]["verified"] is False


def test_reconcile_flags_a_breach_without_lowering_the_mark(store):
    c = FakeClient(book=[stopped_order("AAPL", "88.00")])
    sw.reconcile(c, store)
    out = sw.reconcile(c, store, price_map={"AAPL": 80})
    assert [f["finding"] for f in out["findings"] if f["finding"] == "hw_breach"]
    assert store.hw_get("AAPL")["hw_stop"] == 88.00


def test_reconcile_separates_a_held_position_from_a_stale_mark(store):
    c = FakeClient(book=[stopped_order("HELD", "50.00"), stopped_order("GONE", "40.00")])
    sw.reconcile(c, store)
    c.book = []                                   # both stops vanished from RH
    out = sw.reconcile(c, store, qty_map={"HELD": 10})
    kinds = {f["symbol"]: f["finding"] for f in out["findings"]}
    assert kinds == {"HELD": "missing_stop", "GONE": "orphan_high_water"}
    assert [r["symbol"] for r in out["reset_candidates"]] == ["GONE"]


def test_reconcile_reports_an_unreadable_level(store):
    c = FakeClient(book=[rh_order("AAPL")])       # no stop level, no price
    out = sw.reconcile(c, store)
    assert [f["finding"] for f in out["findings"]] == ["no_level"]
    assert store.hw_get("AAPL") is None


def test_reconcile_reset_candidate_clears_and_lets_a_re_entry_rebuild(store):
    c = FakeClient(book=[stopped_order("AAPL", "88.00")])
    sw.reconcile(c, store)
    c.book = []
    out = sw.reconcile(c, store)
    store.hw_reset(out["reset_candidates"][0]["symbol"], "position closed")
    c.book = [stopped_order("AAPL", "30.00")]     # re-entered far lower
    sw.reconcile(c, store)
    assert store.hw_get("AAPL")["hw_stop"] == 30.00
