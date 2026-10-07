"""Daily sweeps run from the engine loop: equity trailing stops and option take-profit.

Each sweep is independent — its own toggle, auth-service client, and store
handle (held in a SweepState) — so either can be switched off per service.
Both read every setting from app.config.Config; nothing here has its own
defaults.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app import stop_sweeper as sw

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


def maybe_option_take_profit_sweep(config, state):
    """Run the daily option take-profit sweep if enabled and not yet done today.

    Args:
        config: Flask app config (keys from app.config.Config).
        state: This sweep's SweepState.
    """
    if not config["OPTION_TP_ENABLED"]:
        return
    if not config["AUTH_SERVICE_URL"] or not config["RH_AUTH_SERVICE_REQUEST_TOKEN"]:
        return
    state.ensure(config)
    if state.store.option_tp_swept_today():
        return
    if _before_hour_et(config["OPTION_TP_SWEEP_HOUR_ET"]):
        return
    dry = config["OPTION_TP_SWEEP_DRY_RUN"]
    tp = config["OPTION_TP_PERCENT"]
    log.info("[opt-tp] starting daily take-profit sweep "
             "(tp=%.0f%%, dry_run=%s)", tp, dry)
    out = sw.sweep_options_take_profit(state.client, state.store,
                                       tp_percent=tp, dry_run=dry)
    placed = out.get("placed") or []
    log.info("[opt-tp] sweep done: placed=%d skipped=%d",
             len(placed), len(out.get("skipped") or []))
    if dry:
        return
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


def maybe_stop_sweep(config, state, current_positions):
    """Run the daily equity trailing-stop sweep if enabled and not yet done today.

    Mirrors the RH trailing-stop book into sqlite, covers naked tickers with a
    STOP_TRAIL_PERCENT stop, and renews stops near GTC expiry.

    Args:
        config: Flask app config (keys from app.config.Config).
        state: This sweep's SweepState.
        current_positions: Stock positions from BrokerClient.positions().
    """
    if not config["STOP_SWEEP_ENABLED"]:
        return
    state.ensure(config)
    if state.store.swept_today():
        return
    if _before_hour_et(config["STOP_SWEEP_HOUR_ET"]):
        return
    tickers = [t.strip().upper() for t in
               config["STOP_TICKERS"].split(",") if t.strip()]
    qty_map, price_map = {}, {}
    for p in current_positions:
        sym = (p.get("symbol") or "").upper()
        if not sym:
            continue
        qty_map[sym] = p.get("qty")
        q = float(p.get("qty") or 0)
        if q and p.get("market_value"):
            price_map[sym] = float(p["market_value"]) / q
    tickers = sorted(set(tickers) | set(qty_map))
    dry = config["STOP_SWEEP_DRY_RUN"]
    if not dry and not qty_map:
        # Live sweeps need real position sizes; wait for a tick where
        # positions have loaded rather than latching the day.
        log.info("[stop-sweeper] live sweep deferred — positions not loaded yet")
        return
    trail = config["STOP_TRAIL_PERCENT"]
    log.info("[stop-sweeper] starting daily sweep "
             "(tickers=%s, trail=%.0f%%, dry_run=%s)",
             tickers or "book-only", trail, dry)
    out = sw.sweep(state.client, state.store, tickers, trail_percent=trail,
                   dry_run=dry, qty_map=qty_map, price_map=price_map,
                   account_url=sw.account_url_from_box())
    log.info("[stop-sweeper] sweep done: %d active in RH book, "
             "placed=%s, renewed=%s, pruned=%s, skipped=%s",
             out["active_from_rh"],
             [p["symbol"] for p in out["placed"]] or "none",
             [r.get("symbol") for r in out["renewed"]] or "none",
             out["pruned"] or "none",
             [s["symbol"] for s in out.get("skipped", [])] or "none")
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
