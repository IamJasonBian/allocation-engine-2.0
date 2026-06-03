"""IBKRTrader — BrokerClient over ib_async / IB Gateway.

Options are ALWAYS placed as Pegged-to-Stock (``PEG STK``) orders, with an
optional conditional cancel (cancel the working order if the underlying trades
above/below a threshold). All IB calls are marshalled onto the session's
dedicated event-loop thread (see :class:`IBSession`).
"""

import logging

from app.brokers.base import BrokerClient
from app.brokers.ibkr import contracts as C
from app.brokers.ibkr import orders as O
from app.brokers.ibkr import positions as P
from app.brokers.ibkr.session import IBSession

log = logging.getLogger(__name__)


class IBKRTrader(BrokerClient):
    def __init__(
        self,
        account_id: str,
        *,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 1,
        paper: bool = True,
        peg_delta_default: float = 0.5,
        max_option_order_qty: int = 50,
        session: IBSession | None = None,
    ):
        self.account_id = account_id
        self.paper = paper
        self.peg_delta_default = float(peg_delta_default)
        self.max_option_order_qty = int(max_option_order_qty)
        self._session = session or IBSession(host=host, port=port, client_id=client_id)

    # -- account / positions ------------------------------------------------

    def account(self) -> dict:
        try:
            return self._session.run(self._account_async())
        except Exception:
            log.exception("[ibkr] account() failed")
            return {"equity": 0.0, "cash": 0.0, "buying_power": 0.0, "portfolio_value": 0.0}

    async def _account_async(self) -> dict:
        ib = self._session.ib
        rows = await ib.accountSummaryAsync(self.account_id or "")
        return P.map_account_summary(rows)

    def positions(self) -> list[dict]:
        try:
            return self._session.run(self._positions_async())
        except Exception:
            log.exception("[ibkr] positions() failed")
            return []

    async def _positions_async(self) -> list[dict]:
        ib = self._session.ib
        items = ib.portfolio()
        return [m for m in (P.map_position(i) for i in (items or [])) if m]

    def options_positions(self) -> list[dict]:
        try:
            return self._session.run(self._options_positions_async())
        except Exception:
            log.exception("[ibkr] options_positions() failed")
            return []

    async def _options_positions_async(self) -> list[dict]:
        ib = self._session.ib
        out = []
        for item in (ib.portfolio() or []):
            contract = getattr(item, "contract", None)
            if contract is None or getattr(contract, "secType", "") != "OPT":
                continue
            ticker = None
            try:
                tickers = await ib.reqTickersAsync(contract)
                ticker = tickers[0] if tickers else None
            except Exception:
                ticker = None
            mapped = P.map_option_position(item, ticker)
            if mapped:
                out.append(mapped)
        return out

    # -- orders (read) ------------------------------------------------------

    def open_orders(self) -> list[dict]:
        try:
            return self._session.run(self._open_orders_async())
        except Exception:
            log.exception("[ibkr] open_orders() failed")
            return []

    async def _open_orders_async(self) -> list[dict]:
        ib = self._session.ib
        trades = await ib.reqOpenOrdersAsync()
        result = []
        for t in (trades or []):
            contract = getattr(t, "contract", None)
            if contract is not None and getattr(contract, "secType", "") == "OPT":
                continue  # equity open orders only here
            result.append(P.map_open_trade(t))
        return result

    def options_orders(self, limit: int = 50, open_only: bool = False) -> list[dict]:
        try:
            return self._session.run(self._options_orders_async(limit, open_only))
        except Exception:
            log.exception("[ibkr] options_orders() failed")
            return []

    _OPEN_STATES = {"PreSubmitted", "Submitted", "PendingSubmit", "ApiPending"}

    async def _options_orders_async(self, limit: int, open_only: bool) -> list[dict]:
        ib = self._session.ib
        trades = ib.trades()
        result = []
        for t in (trades or []):
            contract = getattr(t, "contract", None)
            if contract is None or getattr(contract, "secType", "") != "OPT":
                continue
            status = getattr(getattr(t, "orderStatus", None), "status", "")
            if open_only and status not in self._OPEN_STATES:
                continue
            result.append(P.map_option_trade(t))
            if len(result) >= limit:
                break
        return result

    # -- equity order submission -------------------------------------------

    def submit_order(self, order: dict) -> dict | None:
        try:
            return self._session.run(self._submit_order_async(order))
        except Exception:
            log.exception("[ibkr] submit_order failed: %s", order)
            return None

    async def _submit_order_async(self, order: dict) -> dict | None:
        ib = self._session.ib
        symbol = order["symbol"]
        qualified = await ib.qualifyContractsAsync(C.build_stock(symbol))
        if not qualified:
            log.error("[ibkr] could not qualify %s", symbol)
            return None
        contract = qualified[0]
        ib_order = O.build_equity_order(
            order["side"], float(order["quantity"]),
            order.get("order_type", "MKT"), order.get("limit_price"),
        )
        trade = ib.placeOrder(contract, ib_order)
        return self._trade_result(trade, symbol)

    # -- option order submission (ALWAYS PEG STK) --------------------------

    def submit_option_order(self, order: dict) -> dict | None:
        try:
            return self._session.run(self._submit_option_order_async(order))
        except Exception:
            log.exception("[ibkr] submit_option_order failed: %s", order)
            return None

    async def _submit_option_order_async(self, order: dict) -> dict | None:
        ib = self._session.ib
        chain_symbol = order["chain_symbol"]
        option_type = order["option_type"]
        side = order["side"]
        qty = float(order["quantity"])

        # 1. Qualify the option contract.
        opt = C.build_option(chain_symbol, order["expiration"],
                             float(order["strike"]), option_type)
        q = await ib.qualifyContractsAsync(opt)
        if not q:
            log.error("[ibkr] could not qualify option %s", order)
            return None
        opt = q[0]

        # 2. Resolve the peg delta: per-order -> live greek -> default.
        delta = order.get("peg_delta")
        if delta is None:
            delta = await self._option_greek_delta(opt)
        if delta is None:
            delta = self.peg_delta_default
        signed = O.signed_delta(option_type, delta)

        # 3. Starting price: explicit limit -> option NBBO midpoint.
        starting = order.get("limit_price")
        if starting is None:
            starting = await self._option_midpoint(opt)

        # 4. Optional cancel-on-cross conditions on the underlying.
        conditions, cancel_flag = [], False
        above = order.get("cancel_if_underlying_above")
        below = order.get("cancel_if_underlying_below")
        if above is not None or below is not None:
            sq = await ib.qualifyContractsAsync(C.build_stock(chain_symbol))
            conid = getattr(sq[0], "conId", 0) if sq else 0
            conditions, cancel_flag = O.build_price_conditions(conid, above, below)

        # 5. Place the PEG STK order; fall back to LMT if the exchange rejects it.
        peg = O.build_peg_stk_order(side, qty, signed, starting, conditions, cancel_flag)
        trade = ib.placeOrder(opt, peg)
        if O.order_is_rejected(trade):
            log.warning("[ibkr] PEG STK rejected for %s — falling back to LMT @ %s",
                        chain_symbol, starting)
            fb = O.build_limit_fallback(side, qty, starting or 0.0, conditions, cancel_flag)
            trade = ib.placeOrder(opt, fb)
        return self._trade_result(trade, chain_symbol)

    async def _option_greek_delta(self, contract):
        try:
            tickers = await self._session.ib.reqTickersAsync(contract)
            mg = getattr(tickers[0], "modelGreeks", None) if tickers else None
            if mg is not None and getattr(mg, "delta", None) is not None:
                return abs(float(mg.delta))
        except Exception:
            log.debug("[ibkr] greek delta unavailable; using default", exc_info=True)
        return None

    async def _option_midpoint(self, contract):
        try:
            tickers = await self._session.ib.reqTickersAsync(contract)
            if tickers:
                mid = tickers[0].midpoint()
                if mid is not None and mid == mid and mid > 0:  # not NaN, positive
                    return float(mid)
        except Exception:
            log.debug("[ibkr] option midpoint unavailable", exc_info=True)
        return None

    @staticmethod
    def _trade_result(trade, symbol: str) -> dict | None:
        order = getattr(trade, "order", None)
        status = getattr(trade, "orderStatus", None)
        if order is None:
            return None
        return {
            "id": str(getattr(order, "orderId", "")),
            "symbol": symbol,
            "status": getattr(status, "status", "") if status else "",
        }

    # -- cancellation -------------------------------------------------------

    def cancel_order(self, order_id: str) -> None:
        try:
            self._session.run(self._cancel_order_async(order_id))
        except Exception:
            log.exception("[ibkr] cancel_order %s failed", order_id)

    async def _cancel_order_async(self, order_id: str):
        ib = self._session.ib
        for t in (ib.openTrades() or []):
            order = getattr(t, "order", None)
            if order is not None and str(getattr(order, "orderId", "")) == str(order_id):
                ib.cancelOrder(order)
                return
        log.warning("[ibkr] cancel_order: no open trade with id %s", order_id)

    def cancel_all(self) -> None:
        try:
            self._session.run(self._cancel_all_async())
        except Exception:
            log.exception("[ibkr] cancel_all failed")

    async def _cancel_all_async(self):
        self._session.ib.reqGlobalCancel()

    # -- auth status --------------------------------------------------------

    def auth_status(self) -> dict:
        return {
            "connected": self._session.is_connected(),
            "paper": self.paper,
            "account_id": self.account_id,
        }
