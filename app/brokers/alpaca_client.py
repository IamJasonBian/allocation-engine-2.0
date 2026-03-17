"""Alpaca trading client — wraps alpaca-py for order submission and account info."""

import logging
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.common.exceptions import APIError

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
        self.data_client = StockHistoricalDataClient(api_key, secret_key)

    # -- account / positions ------------------------------------------------

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

    # -- order submission ---------------------------------------------------

    def submit_order(self, order: dict) -> dict | None:
        symbol = _map_symbol(order["symbol"])
        side = OrderSide.BUY if order["side"] == "BUY" else OrderSide.SELL
        qty = float(order["quantity"])
        otype = order.get("order_type", "market").lower()
        limit_px = order.get("limit_price")
        stop_px = order.get("stop_price")

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
            return {
                "id": str(result.id),
                "symbol": result.symbol,
                "status": result.status.value,
            }

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

    # -- market data -----------------------------------------------------------

    def get_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        """Fetch latest quote prices from Alpaca market data for the given symbols."""
        if not symbols:
            return {}
        try:
            mapped = [_map_symbol(s) for s in symbols]
            req = StockLatestQuoteRequest(symbol_or_symbols=mapped)
            quotes = self.data_client.get_stock_latest_quote(req)
            prices = {}
            for sym, quote in quotes.items():
                # ask_price is more conservative; fall back to bid
                price = float(quote.ask_price) if quote.ask_price else float(quote.bid_price)
                prices[sym] = price
            # Reverse-map symbols back to original names
            reverse_map = {v: k for k, v in SYMBOL_MAP.items()}
            return {reverse_map.get(k, k): v for k, v in prices.items()}
        except Exception:
            log.exception("Failed to fetch Alpaca prices for %s", symbols)
            return {}

    def get_latest_quote(self, symbol: str) -> dict:
        """Fetch detailed quote for a single symbol (bid, ask, last)."""
        mapped = _map_symbol(symbol)
        req = StockLatestQuoteRequest(symbol_or_symbols=[mapped])
        quotes = self.data_client.get_stock_latest_quote(req)
        quote = quotes.get(mapped)
        if not quote:
            raise ValueError(f"No quote found for {symbol}")
        return {
            "symbol": symbol,
            "price": float(quote.ask_price) if quote.ask_price else float(quote.bid_price),
            "bidPrice": float(quote.bid_price) if quote.bid_price else None,
            "askPrice": float(quote.ask_price) if quote.ask_price else None,
            "previousClose": None,
        }
