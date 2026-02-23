"""Alpaca trading client — wraps alpaca-py for order submission and account info."""

import logging
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
)
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce, OrderType
from alpaca.common.exceptions import APIError

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER

log = logging.getLogger(__name__)

# Map runtime-service symbols to Alpaca symbols if they differ.
# BTC = Grayscale Bitcoin Mini Trust ETF (NYSE Arca) — no mapping needed.
SYMBOL_MAP: dict[str, str] = {}


def _map_symbol(symbol: str) -> str:
    return SYMBOL_MAP.get(symbol, symbol)


class AlpacaTrader:
    def __init__(self):
        self.client = TradingClient(
            ALPACA_API_KEY,
            ALPACA_SECRET_KEY,
            paper=ALPACA_PAPER,
        )

    # ── account / positions ──────────────────────────────────────────

    def account(self) -> dict:
        acct = self.client.get_account()
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "portfolio_value": float(acct.portfolio_value),
        }

    def positions(self) -> list[dict]:
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "side": p.side,
                "market_value": float(p.market_value),
                "avg_entry": float(p.avg_entry_price),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_pl_pct": float(p.unrealized_plpc),
            }
            for p in self.client.get_all_positions()
        ]

    def open_orders(self) -> list[dict]:
        return [
            {
                "id": str(o.id),
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": float(o.qty) if o.qty else None,
                "type": o.type.value,
                "limit_price": float(o.limit_price) if o.limit_price else None,
                "stop_price": float(o.stop_price) if o.stop_price else None,
                "status": o.status.value,
            }
            for o in self.client.get_orders()
        ]

    # ── order submission ─────────────────────────────────────────────

    def submit_order(self, order: dict) -> dict | None:
        """Submit a single order derived from the runtime service order format.

        Expected keys: symbol, side (BUY/SELL), quantity, order_type,
                       limit_price (optional), stop_price (optional).
        Returns Alpaca order dict on success, None on failure.
        """
        symbol = _map_symbol(order["symbol"])
        side = OrderSide.BUY if order["side"] == "BUY" else OrderSide.SELL
        qty = float(order["quantity"])
        otype = order.get("order_type", "market").lower()
        limit_px = order.get("limit_price")
        stop_px = order.get("stop_price")

        try:
            if otype == "limit" and limit_px is not None:
                req = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    limit_price=float(limit_px),
                    time_in_force=TimeInForce.GTC,
                )
            elif otype == "stop" and stop_px is not None:
                req = StopOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    stop_price=float(stop_px),
                    time_in_force=TimeInForce.GTC,
                )
            elif otype == "stop_limit" and limit_px and stop_px:
                req = StopLimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    limit_price=float(limit_px),
                    stop_price=float(stop_px),
                    time_in_force=TimeInForce.GTC,
                )
            else:
                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.GTC,
                )

            result = self.client.submit_order(req)
            log.info("Order submitted: %s %s %s @ %s → %s",
                     side.value, qty, symbol, limit_px or "MKT", result.id)
            return {
                "id": str(result.id),
                "symbol": result.symbol,
                "status": result.status.value,
            }

        except APIError as e:
            log.error("Alpaca API error submitting %s %s %s: %s",
                      side.value, qty, symbol, e)
            return None

    def submit_oto(self, sell_order: dict, buy_order: dict) -> dict | None:
        """Submit an OTO pair: SELL primary, BUY triggered on fill.

        When the SELL fills, Alpaca automatically submits the BUY.
        This avoids the wash-trade rejection from having both sides open.
        """
        symbol = _map_symbol(sell_order["symbol"])
        sell_qty = float(sell_order["quantity"])
        sell_price = float(sell_order["limit_price"])
        buy_price = float(buy_order["limit_price"])

        try:
            req = LimitOrderRequest(
                symbol=symbol,
                qty=sell_qty,
                side=OrderSide.SELL,
                limit_price=sell_price,
                time_in_force=TimeInForce.GTC,
                order_class=OrderClass.OTO,
                take_profit=TakeProfitRequest(limit_price=buy_price),
            )
            result = self.client.submit_order(req)
            log.info("OTO submitted: SELL %s %s @ %s → BUY @ %s  (id=%s)",
                     sell_qty, symbol, sell_price, buy_price, result.id)
            return {
                "id": str(result.id),
                "symbol": result.symbol,
                "status": result.status.value,
                "type": "oto",
            }
        except APIError as e:
            log.error("Alpaca API error submitting OTO %s: %s", symbol, e)
            return None

    def cancel_all(self):
        """Cancel all open orders."""
        self.client.cancel_orders()
        log.info("All open orders cancelled")

    def cancel_order(self, order_id: str):
        self.client.cancel_order_by_id(order_id)
        log.info("Cancelled order %s", order_id)
