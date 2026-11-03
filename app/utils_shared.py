"""Sweeps run from the engine loop: equity trailing stops and option take-profit.

Each sweep is independent — its own toggle, auth-service client, and store
handle (held in a SweepState) — so either can be switched off per service.
Both read every setting from app.config.Config; nothing here has its own
defaults.
"""

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app import stop_sweeper as sw
from app.risk.equity_stop_mapping import EQUITY_STOP_MAPPING
from app.risk.option_stop_loss import plan_option_stop_losses

log = logging.getLogger(__name__)


class SweepState:
    """Lazily-built auth-service client and sqlite store for one sweep."""

    def __init__(self):
        self.client = None
        self.store = None

    def ensure(self, config):
        if self.store is None:
            self.store = sw.StopStore(config["STOP_DB_PATH"])
        if self.client is None:
            self.client = sw.BoxClient(
                base=config["AUTH_SERVICE_URL"],
                token=config["RH_AUTH_SERVICE_REQUEST_TOKEN"])


def _before_hour_et(hour):
    return datetime.now(ZoneInfo("America/New_York")).hour < int(hour)


def _post_bot_activity(events, label):
    if not events:
        return
    from app.trading_db import post_bot_activity
    res = post_bot_activity(events)
    log.info("[trading-db] %s: %s", label, (res or {}).get("data", "failed"))


def maybe_option_take_profit_sweep(config, broker, options_positions,
                                   open_option_orders, state):
    """Run the daily option take-profit sweep if enabled and not yet done today.

    Uses the positions and open orders the loop already read; placements go
    through ``broker.place_option_take_profit``.

    Args:
        config: Flask app config (keys from app.config.Config).
        broker: The engine's BrokerClient; needs ``place_option_take_profit``.
        options_positions: Dicts from ``broker.options_positions()``.
        open_option_orders: Open option orders (OrderEvent dicts).
        state: This sweep's SweepState (only its store is used).

    Returns:
        The sweep result dict, or None when gated off.
    """
    if not config["OPTION_TP_ENABLED"] or not hasattr(broker, "place_option_take_profit"):
        return None
    if state.store is None:
        state.store = sw.StopStore(config["STOP_DB_PATH"])
    if state.store.option_tp_swept_today():
        return None
    if _before_hour_et(config["OPTION_TP_SWEEP_HOUR_ET"]):
        return None
    dry = config["OPTION_TP_SWEEP_DRY_RUN"]
    tp = config["OPTION_TP_PERCENT"]
    log.info("[opt-tp] starting daily take-profit sweep "
             "(tp=%.0f%%, dry_run=%s)", tp, dry)
    out = sw.sweep_options_take_profit(broker, state.store, options_positions,
                                       open_option_orders, tp_percent=tp, dry_run=dry)
    placed = out.get("placed") or []
    log.info("[opt-tp] sweep done: placed=%d skipped=%d failed=%d",
             len(placed), len(out.get("skipped") or []), len(out.get("failed") or []))
    if dry:
        return out
    events = []
    for p in placed:
        result = p.get("result") or {}
        if result.get("id"):
            c = p.get("contract") or {}
            events.append({
                "order_id": result["id"],
                "type": "OPTION_TAKE_PROFIT_LIMIT",
                "status": result.get("state", "submitted"),
                "symbol": c.get("chain_symbol", ""),
                "quantity": float((p.get("order") or {}).get("quantity") or 0),
            })
    _post_bot_activity(events, "option TP bot activity")
    return out


def maybe_option_stop_loss_sweep(config, broker, options_positions,
                                 open_option_orders, state):
    """Rest protective stop-limits on long options on the configured interval.

    Uses the positions and open orders the loop already read, so it adds no
    Robinhood reads; only live placements hit the broker.

    Args:
        config: Flask app config (keys from app.config.Config).
        broker: The engine's BrokerClient; needs ``place_option_stop_loss``.
        options_positions: Dicts from ``broker.options_positions()``.
        open_option_orders: Open option orders (OrderEvent dicts).
        state: Dict carrying ``last_run`` (epoch seconds) between passes.

    Returns:
        ``{"planned", "placed", "skipped"}``, or None when gated off.
    """
    if not config["OPTION_SL_ENABLED"] or not hasattr(broker, "place_option_stop_loss"):
        return None
    now = time.time()
    if now - state.get("last_run", 0.0) < config["OPTION_SL_SWEEP_INTERVAL_SECONDS"]:
        return None
    state["last_run"] = now

    orders, skipped = plan_option_stop_losses(
        options_positions, open_option_orders,
        config["OPTION_SL_PERCENT"], config["OPTION_SL_LIMIT_OFFSET_PERCENT"])
    dry = config["OPTION_SL_SWEEP_DRY_RUN"]
    placed, events = [], []
    for o in orders:
        c = o["contract"]
        desc = (f"{c['chain_symbol']} {c['option_type']} {c['strike']:g} {c['expiration']} "
                f"x{o['quantity']} entry ${o['entry']:.2f} stop ${o['stop_price']:.2f} "
                f"limit ${o['limit_price']:.2f} (-{o['stop_pct']:g}%)")
        if dry:
            log.info("[opt-sl] DRY RUN would place %s", desc)
            continue
        result = broker.place_option_stop_loss(o)
        if not result:
            skipped.append({"contract": c, "reason": "broker_rejected"})
            continue
        log.info("[opt-sl] placed %s -> %s", desc, result["id"])
        placed.append({**o, "result": result})
        events.append({
            "order_id": result["id"],
            "type": "OPTION_STOP_LOSS",
            "status": result.get("state", "submitted"),
            "symbol": c["chain_symbol"],
            "quantity": float(o["quantity"]),
        })
    log.info("[opt-sl] planned=%d placed=%d skipped=%d dry_run=%s",
             len(orders), len(placed), len(skipped), dry)
    _post_bot_activity(events, "option SL bot activity")
    return {"planned": orders, "placed": placed, "skipped": skipped}


def maybe_stop_sweep(config, state, current_positions):
    """Run the equity trailing-stop sweep on the configured interval.

    Default is every 3 hours (8 times/day). Mirrors the RH trailing-stop book
    into sqlite, covers naked tickers — including designated mapping names
    (MU, NBIS) with their coded stop-limit percents — and renews stops near
    GTC expiry.

    Args:
        config: Flask app config (keys from app.config.Config).
        state: This sweep's SweepState.
        current_positions: Stock positions from BrokerClient.positions().
    """
    if not config["STOP_SWEEP_ENABLED"]:
        return
    # BoxClient raises SystemExit without these, which would kill the loop.
    if not config["AUTH_SERVICE_URL"] or not config["RH_AUTH_SERVICE_REQUEST_TOKEN"]:
        return
    state.ensure(config)
    if state.store.swept_within(config["STOP_SWEEP_INTERVAL_SECONDS"]):
        return
    if _before_hour_et(config["STOP_SWEEP_HOUR_ET"]):
        return
    tickers = [t.strip().upper() for t in
               config["STOP_TICKERS"].split(",") if t.strip()]
    qty_map, price_map, instrument_map = {}, {}, {}
    for p in current_positions:
        sym = (p.get("symbol") or "").upper()
        if not sym:
            continue
        qty_map[sym] = p.get("qty")
        q = float(p.get("qty") or 0)
        if q and p.get("market_value"):
            price_map[sym] = float(p["market_value"]) / q
        if p.get("instrument"):
            instrument_map[sym] = p["instrument"]
    tickers = sorted(set(tickers) | set(qty_map) | set(EQUITY_STOP_MAPPING))
    dry = config["STOP_SWEEP_DRY_RUN"]
    if not dry and not qty_map:
        # Live sweeps need real position sizes; wait for a tick where
        # positions have loaded rather than latching the day.
        log.info("[stop-sweeper] live sweep deferred — positions not loaded yet")
        return
    trail = config["STOP_TRAIL_PERCENT"]
    log.info("[stop-sweeper] starting sweep "
             "(tickers=%s, trail=%.0f%%, interval=%ds, dry_run=%s)",
             tickers or "book-only", trail,
             config["STOP_SWEEP_INTERVAL_SECONDS"], dry)
    out = sw.sweep(state.client, state.store, tickers, trail_percent=trail,
                   dry_run=dry, qty_map=qty_map, price_map=price_map,
                   account_url=sw.account_url_from_box(),
                   instrument_map=instrument_map)
    log.info("[stop-sweeper] sweep done: %d active in RH book, "
             "placed=%s, simulated=%s, renewed=%s, pruned=%s, skipped=%s, "
             "frozen=%s, unresolved_active=%d",
             out["active_from_rh"],
             [p["symbol"] for p in out["placed"]] or "none",
             [p["symbol"] for p in out["simulated"]] or "none",
             [r.get("symbol") for r in out["renewed"]] or "none",
             out["pruned"] or "none",
             [s["symbol"] for s in out.get("skipped", [])] or "none",
             out.get("frozen") or "none",
             len(out["unresolved_active_orders"]))
    if dry:
        return
    # Live sweeper actions land in the bot-activity feed
    # (de-duped on {order_id}:{status} downstream).
    events = []
    for p in out["placed"]:
        result = p.get("result") or {}
        if result.get("id"):
            events.append({
                "order_id": result["id"],
                "type": "TRAILING_STOP_ORDER",
                "status": result.get("state", "submitted"),
                "symbol": p["symbol"],
                "quantity": float(qty_map.get(p["symbol"]) or 0),
            })
    for r in out["renewed"]:
        result = r.get("result") or {}
        if r.get("action") == "renewed" and result.get("id"):
            events.append({
                "order_id": result["id"],
                "type": "TRAILING_STOP_REPLACED",
                "status": result.get("state", "submitted"),
                "symbol": r.get("symbol", ""),
            })
    _post_bot_activity(events, "bot activity posted")
