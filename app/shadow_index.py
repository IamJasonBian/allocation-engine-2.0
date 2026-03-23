"""Shadow Index Engine — converts live BTC/USD to Grayscale Bitcoin Mini Trust ETF (BTC).

The Grayscale Bitcoin Mini Trust ETF (ticker BTC, NYSE Arca) holds
~0.000367 BTC per share.  On weekends equity markets are closed so the
ETF doesn't trade, but BTC/USD does.  This module:

  1. Fetches the latest BTC/USD price from the data broker (Alpaca).
  2. Converts to a projected ETF share price via the BTC-per-share ratio.
  3. Compares against the last known Friday close.
  4. Produces a ShadowEquity position that the risk pipeline can drift-check.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field

from app.enums import AssetType, RiskEventType
from app.risk.events import RiskEvent

log = logging.getLogger(__name__)


# ── Index definition ─────────────────────────────────────────────────────────

@dataclass
class IndexConfig:
    """Conversion config for a crypto-backed equity index."""
    shadow_symbol: str        # projected ticker name (e.g. "BTC.shadow")
    crypto_symbol: str        # underlying crypto (e.g. "BTC")
    btc_per_share: float      # crypto units per ETF share
    last_close: float | None  # last Friday equity close price ($)


# Grayscale Bitcoin Mini Trust ETF — ticker BTC on NYSE Arca
# ~0.000367 BTC per share (derived from $31.05 close / ~$84,500 BTC)
BTC_MINI = IndexConfig(
    shadow_symbol="BTC.shadow",
    crypto_symbol="BTC",
    btc_per_share=float(os.environ.get("BTC_ETF_RATIO", "0.000367")),
    last_close=None,  # populated at runtime from BTC_ETF_LAST_CLOSE env var
)


# ── Conversion engine ────────────────────────────────────────────────────────

def btc_to_index_price(btc_price: float, config: IndexConfig) -> float:
    """Convert a BTC spot price to projected ETF share price."""
    return btc_price * config.btc_per_share


def build_shadow_position(
    btc_price: float,
    config: IndexConfig,
    qty: float = 0.0,
) -> dict:
    """Build a position dict for the shadow equity.

    Same shape as broker position dicts so downstream code (Redis sync,
    drift check, tick summary) can consume it without changes.
    """
    projected = btc_to_index_price(btc_price, config)
    entry = config.last_close or projected

    unrealized_pl = (projected - entry) * qty if qty else 0.0
    unrealized_pl_pct = (projected - entry) / entry if entry > 0 else 0.0

    return {
        "symbol": config.shadow_symbol,
        "qty": qty,
        "side": "long",
        "market_value": round(projected * qty, 2) if qty else 0.0,
        "avg_entry": entry,
        "current_price": projected,
        "unrealized_pl": round(unrealized_pl, 2),
        "unrealized_pl_pct": round(unrealized_pl_pct, 4),
        "asset_type": AssetType.SHADOW_EQUITY,
        "_source": {
            "crypto_symbol": config.crypto_symbol,
            "btc_price": btc_price,
            "btc_per_share": config.btc_per_share,
            "last_close": config.last_close,
        },
    }


def check_shadow_drift(
    btc_price: float,
    config: IndexConfig,
    threshold: float = 0.08,
) -> RiskEvent | None:
    """Check if the projected ETF price has drifted from the last close.

    Returns a RiskEvent if drift >= threshold, else None.
    """
    if config.last_close is None or config.last_close <= 0:
        return None

    projected = btc_to_index_price(btc_price, config)
    drift = (projected - config.last_close) / config.last_close

    if abs(drift) < threshold:
        log.info(
            "[shadow] %s projected $%.2f vs close $%.2f → drift %+.2f%% (below %.0f%% threshold)",
            config.shadow_symbol, projected, config.last_close,
            drift * 100, threshold * 100,
        )
        return None

    direction = "above" if drift > 0 else "below"
    event = RiskEvent(
        event_type=RiskEventType.PRICE_DEPEG,
        symbol=config.shadow_symbol,
        drift_pct=abs(drift),
        message=(
            f"{config.shadow_symbol} projected ${projected:,.2f} is "
            f"{abs(drift):.2%} {direction} Friday close ${config.last_close:,.2f} "
            f"(BTC/USD ${btc_price:,.2f}) — weekend depeg"
        ),
        metadata={
            "btc_price": btc_price,
            "projected_price": projected,
            "last_close": config.last_close,
            "btc_per_share": config.btc_per_share,
            "direction": direction,
            "asset_type": AssetType.SHADOW_EQUITY,
        },
    )
    log.warning("SHADOW DRIFT: %s", event.message)
    return event


def check_order_shadow_drift(
    btc_price: float,
    config: IndexConfig,
    open_orders: list[dict],
    threshold: float = 0.05,
) -> list[RiskEvent]:
    """Check open limit orders for the BTC ETF against the projected shadow price.

    On weekends, BTC/USD moves but equity limit orders sit at Friday prices.
    If the projected ETF price has diverged from an order's limit_price by more
    than *threshold*, emit a PRICE_DEPEG warning — those orders will likely
    fill at unfavourable prices on Monday open.

    Returns a list of RiskEvents (one per depegged order).
    """
    projected = btc_to_index_price(btc_price, config)
    events: list[RiskEvent] = []

    # Match orders whose symbol is the ETF ticker (e.g. "BTC")
    etf_symbol = config.crypto_symbol  # both use ticker "BTC"
    btc_orders = [
        o for o in open_orders
        if o.get("symbol") == etf_symbol and o.get("limit_price") is not None
    ]

    for o in btc_orders:
        limit_px = float(o["limit_price"])
        if limit_px <= 0:
            continue

        drift = (projected - limit_px) / limit_px
        if abs(drift) < threshold:
            log.info(
                "[shadow-order] %s %s limit $%.2f vs projected $%.2f → drift %+.2f%% (OK)",
                o.get("side", "?"), etf_symbol, limit_px, projected, drift * 100,
            )
            continue

        side = o.get("side", "?")
        direction = "above" if drift > 0 else "below"

        # A buy limit below projected = could fill cheap (good) but may not fill
        # A buy limit above projected = will fill immediately at inflated price
        # A sell limit below projected = will sell at a loss vs real value
        if side.upper() == "BUY" and drift > 0:
            risk_note = "gap_fill"
        elif side.upper() == "SELL" and drift < 0:
            risk_note = "no_fill"
        else:
            risk_note = "diverged"

        event = RiskEvent(
            event_type=RiskEventType.PRICE_DEPEG,
            symbol=f"{etf_symbol}",
            drift_pct=abs(drift),
            message=(
                f"Open {side} limit ${limit_px:.2f} for {etf_symbol} is "
                f"{abs(drift):.2%} {direction} projected ${projected:.2f} "
                f"(BTC/USD ${btc_price:,.2f}) — {risk_note}"
            ),
            metadata={
                "order_id": o.get("id", ""),
                "side": side,
                "limit_price": limit_px,
                "projected_price": projected,
                "btc_price": btc_price,
                "drift_direction": direction,
                "risk_classification": risk_note,
                "asset_type": AssetType.SHADOW_EQUITY,
            },
        )
        log.warning("SHADOW ORDER DRIFT: %s", event.message)
        events.append(event)

    return events
