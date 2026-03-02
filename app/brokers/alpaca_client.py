"""Alpaca trading client — wraps alpaca-py for order submission and account info."""

import logging
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.common.exceptions import APIError

from app.models import (
    AccountSummary, FilledOrder, OpenOrder, OptionOrder,
    OptionPositionData, Order, OrderResult, Position,
)

log = logging.getLogger(__name__)

SYMBOL_MAP = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
}


def _map_symbol(symbol: str) -> str:
    return SYMBOL_MAP.get(symbol, symbol)


class AlpacaTrader:
    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        self.client = TradingClient(api_key, secret_key, paper=paper)

    # -- account / positions ------------------------------------------------

    def account(self) -> AccountSummary:
        acct = self.client.get_account()
        return AccountSummary(
            equity=float(acct.equity),
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
            portfolio_value=float(acct.portfolio_value),
        )

    def positions(self) -> list[Position]:
        return [
            Position(
                symbol=p.symbol,
                qty=float(p.qty),
                side=p.side,
                market_value=float(p.market_value),
                avg_entry=float(p.avg_entry_price),
                unrealized_pl=float(p.unrealized_pl),
                unrealized_pl_pct=float(p.unrealized_plpc),
            )
            for p in self.client.get_all_positions()
        ]

    def open_orders(self) -> list[OpenOrder]:
        return [
            OpenOrder(
                id=str(o.id),
                symbol=o.symbol,
                side=o.side.value,
                qty=float(o.qty) if o.qty else 0.0,
                order_type=o.type.value,
                limit_price=float(o.limit_price) if o.limit_price else None,
                stop_price=float(o.stop_price) if o.stop_price else None,
                status=o.status.value,
            )
            for o in self.client.get_orders()
        ]

    def recent_orders(self, limit: int = 50) -> list[FilledOrder]:
        """Return recent filled/cancelled stock orders."""
        try:
            req = GetOrdersRequest(
                status=QueryOrderStatus.CLOSED,
                limit=limit,
            )
            raw = self.client.get_orders(filter=req)
            return [
                FilledOrder(
                    id=str(o.id),
                    symbol=o.symbol,
                    side=o.side.value,
                    qty=float(o.qty) if o.qty else 0.0,
                    order_type=o.type.value,
                    limit_price=float(o.limit_price) if o.limit_price else None,
                    stop_price=float(o.stop_price) if o.stop_price else None,
                    average_price=float(o.filled_avg_price) if o.filled_avg_price else None,
                    filled_qty=float(o.filled_qty) if o.filled_qty else 0.0,
                    status=o.status.value,
                    created_at=o.created_at.isoformat() if o.created_at else "",
                    updated_at=o.updated_at.isoformat() if o.updated_at else "",
                )
                for o in raw
            ]
        except Exception:
            log.exception("Failed to fetch recent orders from Alpaca")
            return []

    def option_positions(self) -> list[OptionPositionData]:
        """Alpaca does not support options — return empty list."""
        return []

    def recent_option_orders(self, limit: int = 20) -> list[OptionOrder]:
        """Alpaca does not support options — return empty list."""
        return []

    # -- order submission ---------------------------------------------------

    def submit_order(self, order: Order) -> OrderResult | None:
        symbol = _map_symbol(order.symbol)
        side = OrderSide.BUY if order.side == "BUY" else OrderSide.SELL
        qty = order.qty
        otype = order.order_type.lower()
        limit_px = order.limit_price
        stop_px = order.stop_price

        try:
            if otype == "limit" and limit_px is not None:
                req = LimitOrderRequest(
                    symbol=symbol, qty=qty, side=side,
                    limit_price=float(limit_px), time_in_force=TimeInForce.GTC,
                )
            elif otype == "stop" and stop_px is not None:
                req = StopOrderRequest(
                    symbol=symbol, qty=qty, side=side,
                    stop_price=float(stop_px), time_in_force=TimeInForce.GTC,
                )
            elif otype == "stop_limit" and limit_px and stop_px:
                req = StopLimitOrderRequest(
                    symbol=symbol, qty=qty, side=side,
                    limit_price=float(limit_px), stop_price=float(stop_px),
                    time_in_force=TimeInForce.GTC,
                )
            else:
                req = MarketOrderRequest(
                    symbol=symbol, qty=qty, side=side,
                    time_in_force=TimeInForce.GTC,
                )

            result = self.client.submit_order(req)
            log.info("Order submitted: %s %s %s @ %s -> %s",
                     side.value, qty, symbol, limit_px or "MKT", result.id)
            return OrderResult(
                id=str(result.id),
                symbol=result.symbol,
                status=result.status.value,
            )

        except APIError as e:
            log.error("Alpaca API error submitting %s %s %s: %s",
                      side.value, qty, symbol, e)
            return None

    def cancel_all(self):
        self.client.cancel_orders()
        log.info("All open orders cancelled")

    def cancel_order(self, order_id: str):
        self.client.cancel_order_by_id(order_id)
        log.info("Cancelled order %s", order_id)
