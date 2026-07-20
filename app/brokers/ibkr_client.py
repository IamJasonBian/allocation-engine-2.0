"""IBKR trading client — talks to a locally running IBKR Client Portal
Gateway (CP Gateway) over its REST API.

Unlike the Robinhood client, there is no auth-service box for IBKR: the CP
Gateway process itself owns the session. IBKR requires an interactive
browser login against the gateway to establish that session — this cannot
be automated from here, so this client never attempts to log in. It only
talks to an already-authenticated gateway and pings `/tickle` to keep the
session alive, mirroring the "never authenticate ourselves" posture the
Robinhood client takes toward the auth-service box.

Reference: IBKR Client Portal Web API — https://interactivebrokers.github.io/cpwebapi/

Note: IBKR's Client Portal Web API does not expose fund-transfer (ACH
deposit/withdraw) initiation for retail accounts, so the funding methods
below raise NotImplementedError — see BrokerClient's defaults and
app/api/transfer.py for how that surfaces to callers.
"""

import logging

import requests

from app.brokers.base import BrokerClient
from app.enums import OrderType

log = logging.getLogger(__name__)

# Cache symbol -> contract id (conid) resolutions to avoid repeated lookups.
_conid_cache: dict[str, int] = {}

_IBKR_TYPE_TO_ORDER_TYPE = {
    "MKT": OrderType.MARKET,
    "LMT": OrderType.LIMIT,
    "STP": OrderType.STOP,
    "STOP_LIMIT": OrderType.STOP_LIMIT,
}

_OPEN_STATUSES = {"PendingSubmit", "PreSubmitted", "Submitted", "PendingCancel", "ApiPending"}


class IBKRTrader(BrokerClient):
    def __init__(
        self,
        gateway_url: str = "https://localhost:5000/v1/api",
        account_id: str = "",
        verify_ssl: bool = False,
        timeout: int = 15,
    ):
        self.base_url = gateway_url.rstrip("/")
        self.account_id = account_id
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._session = requests.Session()
        if not verify_ssl:
            # CP Gateway ships with a self-signed cert by default.
            requests.packages.urllib3.disable_warnings(
                requests.packages.urllib3.exceptions.InsecureRequestWarning
            )

    # -- low-level request helper --------------------------------------------

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}{path}"
        resp = self._session.request(
            method, url, timeout=self.timeout, verify=self.verify_ssl, **kwargs
        )
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()

    # -- session --------------------------------------------------------------

    def _ensure_auth(self):
        """Verify the CP Gateway session is authenticated. Never logs in —
        the gateway requires an interactive browser login out-of-band."""
        try:
            status = self._request("GET", "/iserver/auth/status")
        except requests.RequestException as e:
            raise RuntimeError(f"IBKR CP Gateway unreachable at {self.base_url}: {e}") from e

        if not status.get("authenticated") and status.get("connected"):
            # SSO session still alive but the brokerage session lapsed —
            # this reauth is a re-handshake, not a login.
            try:
                self._request("POST", "/iserver/reauthenticate")
                status = self._request("GET", "/iserver/auth/status")
            except requests.RequestException:
                pass

        if not status.get("authenticated"):
            gateway_root = self.base_url.split("/v1/api")[0]
            raise RuntimeError(
                "IBKR CP Gateway is not authenticated — log in via the "
                f"gateway's browser UI at {gateway_root}"
            )

        try:
            self._request("POST", "/tickle")
        except requests.RequestException:
            log.warning("IBKR /tickle keep-alive failed", exc_info=True)

    def _resolve_account_id(self) -> str:
        if self.account_id:
            return self.account_id
        accounts = self._request("GET", "/iserver/accounts")
        ids = accounts.get("accounts") or []
        if not ids:
            raise RuntimeError("IBKR gateway returned no accounts")
        self.account_id = ids[0]
        return self.account_id

    # -- contract resolution ---------------------------------------------------

    def _conid(self, symbol: str) -> int:
        symbol = symbol.upper()
        if symbol in _conid_cache:
            return _conid_cache[symbol]
        results = self._request("GET", "/iserver/secdef/search", params={"symbol": symbol}) or []
        for r in results:
            if not isinstance(r, dict) or not r.get("conid"):
                continue
            if r.get("secType", "STK") == "STK":
                conid = int(r["conid"])
                _conid_cache[symbol] = conid
                return conid
        raise ValueError(f"Could not resolve IBKR contract id for symbol {symbol}")

    # -- account / positions ----------------------------------------------------

    def account(self) -> dict:
        self._ensure_auth()
        account_id = self._resolve_account_id()
        summary = self._request("GET", f"/portfolio/{account_id}/summary") or {}

        def amt(key):
            return float((summary.get(key) or {}).get("amount", 0) or 0)

        return {
            "equity": amt("netliquidation"),
            "cash": amt("totalcashvalue"),
            "buying_power": amt("buyingpower"),
            "portfolio_value": amt("netliquidation"),
        }

    def positions(self) -> list[dict]:
        self._ensure_auth()
        account_id = self._resolve_account_id()
        result = []
        page = 0
        while True:
            raw = self._request("GET", f"/portfolio/{account_id}/positions/{page}") or []
            if not raw:
                break
            for pos in raw:
                qty = float(pos.get("position", 0) or 0)
                if qty == 0:
                    continue
                avg_cost = float(pos.get("avgCost", 0) or 0)
                mkt_price = float(pos.get("mktPrice", avg_cost) or avg_cost)
                mkt_value = float(pos.get("mktValue", qty * mkt_price) or (qty * mkt_price))
                cost_basis = qty * avg_cost
                unrealized_pl = mkt_value - cost_basis
                result.append({
                    "symbol": pos.get("ticker") or pos.get("contractDesc", "UNKNOWN"),
                    "qty": qty,
                    "side": "long" if qty > 0 else "short",
                    "market_value": round(mkt_value, 2),
                    "avg_entry": avg_cost,
                    "unrealized_pl": round(unrealized_pl, 2),
                    "unrealized_pl_pct": round(unrealized_pl / cost_basis, 4) if cost_basis else 0.0,
                })
            if len(raw) < 100:  # IBKR paginates positions at 100/page
                break
            page += 1
        return result

    def open_orders(self) -> list[dict]:
        self._ensure_auth()
        raw = self._request("GET", "/iserver/account/orders") or {}
        orders = raw.get("orders", []) if isinstance(raw, dict) else []
        result = []
        for o in orders:
            if not isinstance(o, dict) or o.get("status") not in _OPEN_STATUSES:
                continue
            qty = o.get("remainingQuantity")
            if qty is None:
                qty = o.get("totalSize", 0)
            result.append({
                "id": str(o.get("orderId", "")),
                "symbol": o.get("ticker", ""),
                "side": (o.get("side") or "").upper(),
                "qty": float(qty or 0),
                "type": _IBKR_TYPE_TO_ORDER_TYPE.get(o.get("orderType", ""), "market"),
                "limit_price": float(o["price"]) if o.get("price") else None,
                "stop_price": float(o["auxPrice"]) if o.get("auxPrice") else None,
                "status": o.get("status", "unknown"),
            })
        return result

    # -- order submission ---------------------------------------------------

    def submit_order(self, order: dict) -> dict | None:
        self._ensure_auth()
        account_id = self._resolve_account_id()
        symbol = order["symbol"]
        side = order["side"].upper()
        qty = float(order["quantity"])
        otype = order.get("order_type", OrderType.MARKET).lower()
        limit_px = order.get("limit_price")
        stop_px = order.get("stop_price")

        try:
            conid = self._conid(symbol)
        except ValueError as e:
            log.error("IBKR order error: %s", e)
            return None

        payload = {"conid": conid, "side": side, "quantity": qty, "tif": "GTC", "acctId": account_id}
        if otype == OrderType.LIMIT and limit_px is not None:
            payload["orderType"] = "LMT"
            payload["price"] = float(limit_px)
        elif otype == OrderType.STOP and stop_px is not None:
            payload["orderType"] = "STP"
            payload["auxPrice"] = float(stop_px)
        elif otype == OrderType.STOP_LIMIT and limit_px is not None and stop_px is not None:
            payload["orderType"] = "STOP_LIMIT"
            payload["price"] = float(limit_px)
            payload["auxPrice"] = float(stop_px)
        else:
            payload["orderType"] = "MKT"

        try:
            resp = self._request(
                "POST", f"/iserver/account/{account_id}/orders", json={"orders": [payload]}
            )
            resp = self._confirm_order_questions(resp)

            if isinstance(resp, list) and resp:
                first = resp[0]
                order_id = first.get("order_id") or first.get("orderId")
                if order_id:
                    log.info("IBKR order submitted: %s %s %s @ %s -> %s",
                             side, qty, symbol, limit_px or stop_px or "MKT", order_id)
                    return {
                        "id": str(order_id),
                        "symbol": symbol,
                        "status": first.get("order_status", "submitted"),
                    }
            log.error("IBKR order response had no order id: %s", resp)
            return None
        except requests.RequestException as e:
            log.error("IBKR order error for %s %s %s: %s", side, qty, symbol, e)
            return None

    def _confirm_order_questions(self, resp, _depth: int = 0):
        """IBKR sometimes replies with precaution questions (e.g. an order
        value warning) instead of a fill — auto-confirm up to a few rounds
        rather than leaving the order stuck unconfirmed."""
        if _depth > 5 or not isinstance(resp, list):
            return resp
        pending = [r for r in resp if isinstance(r, dict) and r.get("id") and "message" in r]
        if not pending:
            return resp
        for q in pending:
            resp = self._request("POST", f"/iserver/reply/{q['id']}", json={"confirmed": True})
        return self._confirm_order_questions(resp, _depth + 1)

    def cancel_order(self, order_id: str):
        self._ensure_auth()
        account_id = self._resolve_account_id()
        self._request("DELETE", f"/iserver/account/{account_id}/order/{order_id}")
        log.info("Cancelled IBKR order %s", order_id)

    def cancel_all(self):
        """IBKR's CPAPI has no bulk-cancel endpoint — cancel each open order."""
        for o in self.open_orders():
            try:
                self.cancel_order(o["id"])
            except requests.RequestException:
                log.exception("Failed to cancel IBKR order %s", o["id"])
        log.info("All IBKR orders cancelled")

    # -- auth status ----------------------------------------------------------

    def auth_status(self) -> dict:
        try:
            status = self._request("GET", "/iserver/auth/status")
        except requests.RequestException as e:
            return {"authenticated": False, "connected": False, "error": str(e),
                     "gateway_url": self.base_url}
        return {
            "authenticated": bool(status.get("authenticated")),
            "connected": bool(status.get("connected")),
            "competing": bool(status.get("competing")),
            "gateway_url": self.base_url,
        }

    # -- funding: not available via IBKR's retail Web API ---------------------
    # IBKR's Client Portal Web API does not expose ACH deposit/withdraw
    # initiation for retail accounts — funding must be done through the
    # Client Portal web or mobile app. These overrides just give a more
    # specific message than BrokerClient's generic default.

    def linked_bank_accounts(self) -> list[dict]:
        raise NotImplementedError(
            "IBKR's Client Portal Web API does not expose linked bank "
            "accounts — manage bank instructions via the Client Portal "
            "web or mobile app."
        )

    def deposit(self, amount: float, **kwargs) -> dict | None:
        raise NotImplementedError(
            "IBKR's Client Portal Web API does not support initiating ACH "
            "deposits — use the Client Portal web or mobile app."
        )

    def withdraw(self, amount: float, **kwargs) -> dict | None:
        raise NotImplementedError(
            "IBKR's Client Portal Web API does not support initiating ACH "
            "withdrawals — use the Client Portal web or mobile app."
        )

    def transfer_history(self, **kwargs) -> list[dict]:
        raise NotImplementedError(
            "IBKR's Client Portal Web API does not expose transfer history — "
            "check the Client Portal web or mobile app."
        )
