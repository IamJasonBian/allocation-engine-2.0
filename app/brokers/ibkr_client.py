"""IBKR trading client — wraps ib_async against a TWS / IB Gateway socket.

Unlike Alpaca (REST) and Robinhood (token vended by the auth-service box),
IBKR routing needs a live socket to a running TWS or IB Gateway process, and
that socket is unauthenticated by design. Where that process lives — and why
this client must only ever dial localhost or an SSH tunnel, never a public
address — is docs/IBKR_GATEWAY.md.

The IB client is imported lazily so deployments that never enable the broker
(ENABLED_BROKERS without "ibkr") don't need it installed.
"""

import asyncio
import logging

from app.brokers.base import BrokerClient
from app.enums import OrderSide as AppOrderSide, OrderType

log = logging.getLogger(__name__)

# IBKR order-type codes → app OrderType values (open_orders reporting).
_IB_TYPE_MAP = {
    "MKT": OrderType.MARKET,
    "LMT": OrderType.LIMIT,
    "STP": OrderType.STOP,
    "STP LMT": OrderType.STOP_LIMIT,
}


def _load_ib_client():
    """Load maintained ib_async, with ib_insync fallback for old gateway boxes."""
    try:
        import ib_async  # type: ignore
        return ib_async
    except ImportError:
        try:
            import ib_insync  # type: ignore
            return ib_insync
        except ImportError as e:
            raise ImportError(
                "ib_async is required for the ibkr broker. Install with: pip install ib_async"
            ) from e


class IBKRTrader(BrokerClient):
    def __init__(self, host: str, port: int, client_id: int, paper: bool = True):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.paper = paper
        self._ib = None  # ib_async.IB, created on first connect

    # -- connection -----------------------------------------------------------

    def _connect(self):
        """Dial the gateway socket if not already connected. Returns the IB handle."""
        ib_client = _load_ib_client()
        if self._ib is None:
            # The engine loop runs in a daemon thread; the IB client needs an
            # asyncio loop in whichever thread touches it.
            try:
                asyncio.get_event_loop()
            except RuntimeError:
                asyncio.set_event_loop(asyncio.new_event_loop())
            self._ib = ib_client.IB()
        if not self._ib.isConnected():
            try:
                self._ib.connect(self.host, self.port, clientId=self.client_id)
                log.info("IBKR connected %s:%s clientId=%s (%s)",
                         self.host, self.port, self.client_id,
                         "paper" if self.paper else "LIVE")
            except Exception as e:
                raise ConnectionError(
                    f"Could not reach IB Gateway/TWS at {self.host}:{self.port}. "
                    f"The gateway process must be running with the API socket enabled "
                    f"(see docs/IBKR_GATEWAY.md). Underlying error: {e}"
                ) from e
        return self._ib

    def _stock(self, symbol: str):
        ib_client = _load_ib_client()
        return ib_client.Stock(symbol, "SMART", "USD")

    def _contract(self, order: dict):
        """Build a stock or single-leg option contract from canonical fields."""
        if order.get("asset_class", "stock").lower() not in ("option", "opt"):
            return self._stock(order["symbol"])

        missing = [
            key for key in ("expiration", "strike", "option_type")
            if order.get(key) in (None, "")
        ]
        if missing:
            raise ValueError(f"Option order missing required fields: {', '.join(missing)}")

        right = str(order["option_type"])[0].upper()
        if right not in ("C", "P"):
            raise ValueError("option_type must be call/put or C/P")
        expiry = str(order["expiration"]).replace("-", "")
        ib_client = _load_ib_client()
        return ib_client.Option(
            order["symbol"], expiry, float(order["strike"]), right,
            order.get("exchange", "SMART"), order.get("currency", "USD"),
        )

    # -- account / positions ------------------------------------------------

    def account(self) -> dict:
        ib = self._connect()
        tags = {row.tag: row.value for row in ib.accountSummary()}

        def _f(tag):
            try:
                return float(tags[tag])
            except (KeyError, TypeError, ValueError):
                return 0.0

        return {
            "equity": _f("NetLiquidation"),
            "cash": _f("TotalCashValue"),
            "buying_power": _f("BuyingPower"),
            "portfolio_value": _f("NetLiquidation"),
        }

    def positions(self) -> list[dict]:
        ib = self._connect()
        out = []
        # portfolio() carries marketValue/unrealizedPNL; positions() does not.
        for item in ib.portfolio():
            qty = float(item.position)
            market_value = float(item.marketValue)
            cost_basis = abs(qty) * float(item.averageCost)
            out.append({
                "symbol": item.contract.symbol,
                "qty": qty,
                "side": "long" if qty >= 0 else "short",
                "market_value": market_value,
                "avg_entry": float(item.averageCost),
                "unrealized_pl": float(item.unrealizedPNL),
                "unrealized_pl_pct": (
                    float(item.unrealizedPNL) / cost_basis if cost_basis else 0.0
                ),
            })
        return out

    def portfolio_detail(self) -> list[dict]:
        """Portfolio rows with contract detail preserved (options included).

        positions() flattens to the cross-broker shape and drops option
        fields; the wheel-lanes view needs sec_type/right/strike/expiry to
        tell a short put from a covered call, so this keeps them.
        """
        ib = self._connect()
        out = []
        for item in ib.portfolio():
            contract = item.contract
            sec_type = getattr(contract, "secType", None)
            out.append({
                "symbol": contract.symbol,
                "sec_type": sec_type,
                "right": getattr(contract, "right", None) if sec_type == "OPT" else None,
                "strike": float(contract.strike) if sec_type == "OPT" else None,
                "expiry": getattr(contract, "lastTradeDateOrContractMonth", None)
                          if sec_type == "OPT" else None,
                "qty": float(item.position),
                "avg_cost": float(item.averageCost),
                "market_value": float(item.marketValue),
                "unrealized_pl": float(item.unrealizedPNL),
            })
        return out

    def session_trades(self) -> list[dict]:
        """Every order this API session has seen (open and terminal), for the
        order funnel. Scope is the session: IBKR replays only this clientId's
        orders on connect, so counts reset when the socket does.
        """
        ib = self._connect()
        out = []
        for trade in ib.trades():
            order = trade.order
            status = trade.orderStatus
            is_limit = order.orderType in ("LMT", "STP LMT")
            out.append({
                "id": str(order.orderId),
                "symbol": trade.contract.symbol,
                "sec_type": getattr(trade.contract, "secType", None),
                "side": order.action.lower(),
                "qty": float(order.totalQuantity),
                "order_type": _IB_TYPE_MAP.get(order.orderType, order.orderType.lower()),
                "limit_price": float(order.lmtPrice) if is_limit else None,
                "status": status.status,
                "filled_qty": float(status.filled),
                "avg_fill_price": float(status.avgFillPrice) or None,
            })
        return out

    def executions(self) -> list[dict]:
        """Today's fills (reqExecutions covers the day, not just this session)."""
        ib = self._connect()
        out = []
        for fill in ib.reqExecutions():
            ex = fill.execution
            out.append({
                "exec_id": ex.execId,
                "order_id": str(ex.orderId),
                "symbol": fill.contract.symbol,
                "sec_type": getattr(fill.contract, "secType", None),
                "side": "buy" if ex.side == "BOT" else "sell",
                "qty": float(ex.shares),
                "price": float(ex.price),
                "time": str(ex.time),
            })
        return out

    def open_orders(self) -> list[dict]:
        ib = self._connect()
        # Account-wide: reqAllOpenOrders surfaces orders from every clientId and
        # from TWS/gateway itself, not just this session. openTrades() alone is
        # client-scoped and silently misses orders placed under another clientId
        # (e.g. after a reconnect), which would make the engine misjudge the book.
        ib.reqAllOpenOrders()
        out = []
        for trade in ib.openTrades():
            order = trade.order
            out.append({
                "id": str(order.orderId),
                "symbol": trade.contract.symbol,
                "side": order.action.lower(),
                "qty": float(order.totalQuantity),
                "type": _IB_TYPE_MAP.get(order.orderType, order.orderType.lower()),
                "limit_price": float(order.lmtPrice) if order.orderType in ("LMT", "STP LMT") else None,
                "stop_price": float(order.auxPrice) if order.orderType in ("STP", "STP LMT") else None,
                "status": trade.orderStatus.status,
            })
        return out

    # -- order submission ---------------------------------------------------

    def submit_order(self, order: dict) -> dict | None:
        ib_client = _load_ib_client()
        symbol = order["symbol"]
        action = "BUY" if order["side"] == AppOrderSide.BUY else "SELL"
        qty = float(order["quantity"])
        otype = order.get("order_type", OrderType.MARKET).lower()
        limit_px = order.get("limit_price")
        stop_px = order.get("stop_price")

        if otype == OrderType.LIMIT and limit_px is not None:
            ib_order = ib_client.LimitOrder(action, qty, float(limit_px))
        elif otype == OrderType.STOP and stop_px is not None:
            ib_order = ib_client.StopOrder(action, qty, float(stop_px))
        elif otype == OrderType.STOP_LIMIT and limit_px is not None and stop_px is not None:
            ib_order = ib_client.StopLimitOrder(action, qty, float(limit_px), float(stop_px))
        else:
            ib_order = ib_client.MarketOrder(action, qty)
        ib_order.tif = "GTC"

        try:
            ib = self._connect()
            contract = self._contract(order)
            ib.qualifyContracts(contract)
            trade = ib.placeOrder(contract, ib_order)
            log.info("Order submitted: %s %s %s @ %s -> %s",
                     action, qty, symbol, limit_px or "MKT", trade.order.orderId)
            return {
                "id": str(trade.order.orderId),
                "symbol": symbol,
                "status": trade.orderStatus.status,
            }
        except Exception as e:
            log.error("IBKR error submitting %s %s %s: %s", action, qty, symbol, e)
            return None

    def cancel_order(self, order_id: str):
        ib = self._connect()
        ib.reqAllOpenOrders()   # see orders from other clients too, so we can cancel them
        for trade in ib.openTrades():
            if str(trade.order.orderId) == str(order_id):
                ib.cancelOrder(trade.order)
                log.info("Cancelled order %s", order_id)
                return
        log.warning("cancel_order: no open order with id %s", order_id)

    def cancel_all(self):
        ib = self._connect()
        ib.reqGlobalCancel()
        log.info("All open orders cancelled (reqGlobalCancel)")
