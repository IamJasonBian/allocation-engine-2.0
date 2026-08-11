"""IBKR trading client — talks to a running IBKR Client Portal Web API gateway.

This client does not manage the gateway's brokerage session: the Client
Portal Gateway (clientportal.gw) must already be running and authenticated
(browser-based login + 2FA) on a box we control (Tailscale / GCP), with
IBKR_BASE_URL pointing at it. That login/refresh lifecycle is infrastructure,
not something this client can do headlessly.

CPAPI quirks handled here:
  - Sessions go idle-timeout after a few minutes without traffic, so every
    call tickles the gateway first (`/tickle`) to keep it alive within its
    24h authenticated window.
  - Placing an order can come back as one or more confirmation "replies"
    (risk/warning messages) that must be POSTed back with `{"confirmed":
    true}` before the order actually gets accepted. We auto-confirm since
    this runs unattended, logging each message we wave through.
"""

import logging

import requests
import urllib3

from app.brokers.base import BrokerClient
from app.enums import OrderType

log = logging.getLogger(__name__)

_ORDER_TYPE_MAP = {
    OrderType.MARKET: "MKT",
    OrderType.LIMIT: "LMT",
    OrderType.STOP: "STP",
    OrderType.STOP_LIMIT: "STP_LMT",
}
_REVERSE_ORDER_TYPE_MAP = {v: k for k, v in _ORDER_TYPE_MAP.items()}

_POSITIONS_PAGE_SIZE = 100


class IBKRTrader(BrokerClient):
    def __init__(self, base_url: str, account_id: str, verify_ssl: bool = False, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._conid_cache: dict[str, int] = {}
        self._portfolio_context_ready = False

        if not verify_ssl:
            # The gateway typically presents a self-signed cert on a private
            # network (Tailscale / GCP box) — this is expected, not a threat.
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # -- transport ------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _get(self, path: str, params: dict | None = None, tickle: bool = True):
        if tickle:
            self._tickle()
        resp = requests.get(self._url(path), params=params,
                             verify=self.verify_ssl, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else None

    def _post(self, path: str, payload: dict, tickle: bool = True):
        if tickle:
            self._tickle()
        resp = requests.post(self._url(path), json=payload,
                              verify=self.verify_ssl, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else None

    def _delete(self, path: str, params: dict | None = None, tickle: bool = True):
        if tickle:
            self._tickle()
        resp = requests.delete(self._url(path), params=params,
                                verify=self.verify_ssl, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else None

    def _tickle(self):
        """Best-effort keep-alive, called before every other CPAPI request
        (see _get/_post/_delete) so a stale session never silently skips it.
        A failed tickle just means the next real call surfaces the error."""
        try:
            self._post("/tickle", {}, tickle=False)
        except requests.RequestException:
            log.warning("[ibkr] tickle failed — gateway session may need re-auth")

    # -- symbol resolution ------------------------------------------------------

    def _resolve_conid(self, symbol: str) -> int | None:
        if symbol in self._conid_cache:
            return self._conid_cache[symbol]
        try:
            results = self._get("/iserver/secdef/search", params={"symbol": symbol}) or []
        except requests.RequestException:
            log.exception("[ibkr] secdef search failed for %s", symbol)
            return None

        conid = None
        for item in results:
            if item.get("symbol", "").upper() != symbol.upper():
                continue
            if any(s.get("secType") == "STK" for s in item.get("sections", [])):
                conid = item.get("conid")
                break
        if conid is not None:
            self._conid_cache[symbol] = int(conid)
        return conid

    # -- account / positions ------------------------------------------------

    def _ensure_portfolio_context(self):
        """CPAPI requires /portfolio/accounts to have been called at least
        once before any /portfolio/{accountId}/* endpoint will respond —
        this establishes the session's account context."""
        if self._portfolio_context_ready:
            return
        self._get("/portfolio/accounts")
        self._portfolio_context_ready = True

    def account(self) -> dict:
        self._ensure_portfolio_context()
        summary = self._get(f"/portfolio/{self.account_id}/summary") or {}

        def amt(key):
            v = summary.get(key)
            if isinstance(v, dict):
                return float(v.get("amount", 0) or 0)
            return float(v or 0)

        return {
            "equity": amt("netliquidation"),
            "cash": amt("totalcashvalue") or amt("availablefunds"),
            "buying_power": amt("buyingpower"),
            "portfolio_value": amt("netliquidation"),
        }

    def positions(self) -> list[dict]:
        """Fetch every position page — CPAPI returns up to 100 positions per
        zero-indexed page, so keep paging until a short page signals the end."""
        self._ensure_portfolio_context()
        result = []
        page = 0
        while True:
            raw = self._get(f"/portfolio/{self.account_id}/positions/{page}") or []
            for p in raw:
                qty = float(p.get("position", 0) or 0)
                if qty == 0:
                    continue
                avg_cost = float(p.get("avgCost", 0) or 0)
                market_value = float(p.get("mktValue", 0) or 0)
                unrealized_pl = float(p.get("unrealizedPnl", 0) or 0)
                cost_basis = qty * avg_cost
                result.append({
                    "symbol": p.get("ticker") or p.get("contractDesc", ""),
                    "qty": qty,
                    "side": "long" if qty > 0 else "short",
                    "market_value": round(market_value, 2),
                    "avg_entry": avg_cost,
                    "unrealized_pl": round(unrealized_pl, 2),
                    "unrealized_pl_pct": round(unrealized_pl / abs(cost_basis), 4) if cost_basis else 0.0,
                })
            if len(raw) < _POSITIONS_PAGE_SIZE:
                break
            page += 1
        return result

    def open_orders(self) -> list[dict]:
        data = self._get("/iserver/account/orders", params={"accountId": self.account_id}) or {}
        raw = data.get("orders", []) if isinstance(data, dict) else []
        result = []
        for o in raw:
            ibkr_type = o.get("orderType", "")
            price = o.get("price")
            aux_price = o.get("auxPrice")
            price_f = float(price) if price not in (None, "") else None
            aux_price_f = float(aux_price) if aux_price not in (None, "") else None
            # STP carries the stop price in `price`; STP_LMT carries the
            # limit price in `price` and the stop price in `auxPrice`.
            if ibkr_type == "STP":
                limit_price, stop_price = None, price_f
            elif ibkr_type == "STP_LMT":
                limit_price, stop_price = price_f, aux_price_f
            else:
                limit_price, stop_price = price_f, None
            result.append({
                "id": str(o.get("orderId", "")),
                "symbol": o.get("ticker", ""),
                "side": (o.get("side") or "").upper(),
                "qty": float(o.get("totalSize", 0) or 0),
                "type": _REVERSE_ORDER_TYPE_MAP.get(ibkr_type, "market"),
                "limit_price": limit_price,
                "stop_price": stop_price,
                "status": o.get("status", "unknown"),
            })
        return result

    # -- order submission ---------------------------------------------------

    def _confirm_replies(self, data, max_rounds: int = 5):
        """Auto-confirm IBKR's order-reply chain (risk warnings, etc.)."""
        for _ in range(max_rounds):
            if not isinstance(data, list) or not data:
                return data
            item = data[0]
            reply_id = item.get("id")
            already_placed = "order_id" in item or "orderId" in item
            if reply_id and not already_placed:
                log.warning("[ibkr] auto-confirming order message: %s", item.get("message"))
                data = self._post(f"/iserver/reply/{reply_id}", {"confirmed": True})
            else:
                return data
        return data

    def submit_order(self, order: dict) -> dict | None:
        symbol = order["symbol"]
        side = order["side"].upper()
        qty = float(order["quantity"])
        otype = order.get("order_type", OrderType.MARKET).lower()
        limit_px = order.get("limit_price")
        stop_px = order.get("stop_price")

        conid = self._resolve_conid(symbol)
        if conid is None:
            log.error("[ibkr] could not resolve conid for symbol %s", symbol)
            return None

        ibkr_order = {
            "conid": conid,
            "orderType": _ORDER_TYPE_MAP.get(otype, "MKT"),
            "side": side,
            "quantity": qty,
            "tif": "GTC",
            "acctId": self.account_id,
        }
        # CPAPI price fields differ by order type: LMT/STP_LMT carry the
        # limit price in `price`; STP carries its stop price in `price`
        # instead (there's no separate limit leg); STP_LMT's stop price
        # goes in `auxPrice`.
        if otype == OrderType.LIMIT and limit_px is not None:
            ibkr_order["price"] = float(limit_px)
        elif otype == OrderType.STOP and stop_px is not None:
            ibkr_order["price"] = float(stop_px)
        elif otype == OrderType.STOP_LIMIT and limit_px is not None and stop_px is not None:
            ibkr_order["price"] = float(limit_px)
            ibkr_order["auxPrice"] = float(stop_px)

        try:
            resp = self._post(f"/iserver/account/{self.account_id}/orders",
                               {"orders": [ibkr_order]})
            result = self._confirm_replies(resp)
        except requests.RequestException as e:
            log.error("IBKR order error for %s %s %s: %s", side, qty, symbol, e)
            return None

        if not isinstance(result, list) or not result:
            return None
        item = result[0]
        order_id = item.get("order_id") or item.get("orderId")
        if not order_id:
            log.error("[ibkr] order submission returned no order id: %s", item)
            return None
        log.info("IBKR order submitted: %s %s %s @ %s -> %s",
                 side, qty, symbol, limit_px or "MKT", order_id)
        return {
            "id": str(order_id),
            "symbol": symbol,
            "status": item.get("order_status", "submitted"),
        }

    def cancel_order(self, order_id: str):
        self._delete(f"/iserver/account/{self.account_id}/order/{order_id}",
                      params={"accountId": self.account_id})
        log.info("Cancelled IBKR order %s", order_id)

    def cancel_all(self):
        for o in self.open_orders():
            self.cancel_order(o["id"])
        log.info("All open IBKR orders cancelled")

    # -- funding --------------------------------------------------------------

    def deposit(self, amount: float) -> dict | None:
        """IBKR's Client Portal Web API has no public deposit endpoint for
        retail accounts — funding is initiated via the Client Portal UI
        (bank link + DocuSign), not this API. Not implementable here."""
        raise NotImplementedError(
            "IBKR does not expose a retail deposit/funding endpoint via the "
            "Client Portal Web API — initiate deposits from the Client "
            "Portal UI instead."
        )

    def withdraw(self, amount: float) -> dict | None:
        """See deposit() — same API gap for withdrawals."""
        raise NotImplementedError(
            "IBKR does not expose a retail withdrawal endpoint via the "
            "Client Portal Web API — initiate withdrawals from the Client "
            "Portal UI instead."
        )
