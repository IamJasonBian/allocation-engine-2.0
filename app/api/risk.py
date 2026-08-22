"""Metrics & risk API: the full report and its sections.

    GET /api/risk/report              everything (what the /risk page loads)
    GET /api/risk/summary             headline numbers + flags only
    GET /api/risk/<section>           performance | drawdown | exposure | covariance |
                                      tail | stress | symbols | curve
    GET /api/risk/symbol/<symbol>     one ticker's risk row + mark series

`?broker=` selects the broker; `?refresh=1` bypasses the short cache. Data
comes from the broker facade (local SQLite in dev, the auth box in prod), the
same path `/api/pnl/risk` uses.
"""

import threading
import time

from flask import Blueprint, current_app, jsonify, request

from app.brokers import get_broker
from app.pnl import pnl_risk
from app.risk import build_report

bp = Blueprint("risk", __name__)

SECTIONS = ("performance", "drawdown", "exposure", "covariance", "tail", "stress", "symbols", "curve")
CACHE_SECONDS = 60
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _load_book(broker_name: str) -> tuple[list[dict], dict[str, list[dict]]]:
    broker = get_broker(broker_name)
    if not hasattr(broker, "trade_fills") or not hasattr(broker, "price_history"):
        raise ValueError(f"Broker {broker_name} does not support fills + price history")
    fills = broker.trade_fills()
    symbols = sorted({f["symbol"].upper() for f in fills})
    closes = {sym: broker.price_history(sym) for sym in symbols}
    return fills, closes


def _report(broker_name: str, refresh: bool = False) -> dict:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(broker_name)
        if hit and not refresh and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    fills, closes = _load_book(broker_name)
    report = build_report(fills, closes)
    report["broker"] = broker_name
    with _lock:
        _cache[broker_name] = (time.monotonic(), report)
    return report


def _broker() -> str:
    return request.args.get("broker") or current_app.config["DEFAULT_BROKER"]


def _refresh() -> bool:
    return request.args.get("refresh") in ("1", "true")


@bp.route("/risk/report")
def risk_report():
    """Full metrics & risk report."""
    try:
        return jsonify(_report(_broker(), _refresh()))
    except Exception as e:  # surfaced inline by the page
        return jsonify({"error": str(e)}), 500


@bp.route("/risk/summary")
def risk_summary():
    """Headline numbers and flags — the Telegram/ops-check payload."""
    try:
        rep = _report(_broker(), _refresh())
        return jsonify({k: rep[k] for k in ("broker", "generatedAt", "asOf", "headline", "flags")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/risk/<section>")
def risk_section(section):
    """One section of the report."""
    if section not in SECTIONS:
        return jsonify({"error": f"unknown section {section!r}", "sections": list(SECTIONS)}), 404
    try:
        rep = _report(_broker(), _refresh())
        return jsonify({"broker": rep["broker"], "asOf": rep["asOf"], section: rep[section]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/risk/symbol/<symbol>")
def risk_symbol(symbol):
    """One ticker's risk model plus its daily mark series."""
    broker_name = _broker()
    symbol = symbol.upper()
    try:
        broker = get_broker(broker_name)
        data = pnl_risk(broker.trade_fills(), broker.price_history(symbol), symbol)
        return jsonify({"broker": broker_name, "symbol": symbol, **data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
