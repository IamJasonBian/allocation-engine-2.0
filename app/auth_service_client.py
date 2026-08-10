"""Client for the standalone auth-service that holds the Robinhood session.

The auth-service runs on an external box, authenticates to Robinhood, and
exposes a small HTTP surface. This service reaches it with a Bearer token
(`RH_AUTH_SERVICE_REQUEST_TOKEN`) over HTTPS.

Two order calls are allow-listed as direct (non-MCP) calls against the
auth-service, using its Robinhood password session:
    GET  /orders/trailing_stop   — read active percentage trailing-stop orders
    POST /orders/trailing_stop   — build/place a trailing-stop order
Everything else is routed through the MCP passthrough (POST /exec/mcp), which
relays a JSON-RPC 2.0 payload to the official Robinhood MCP
(agent.robinhood.com/mcp/trading); the MCP's own OAuth token is attached by the
auth-service.

If the auth-service reports it needs an OTP / device approval, we log
``OTP needed`` and raise ``OTPRequired`` so the caller can surface it.
"""

import logging
import uuid

import requests

from app.config import Config

log = logging.getLogger(__name__)

# Auth-service statuses / error codes that mean a human must approve a device
# push or provide an OTP before the session can proceed.
_OTP_MARKERS = {"MFA_REQUIRED", "OTP_REQUIRED", "DEVICE_APPROVAL_NEEDED"}


class AuthServiceError(RuntimeError):
    """Auth-service call failed (network, non-2xx, or bad response)."""


class AuthServiceNotConfigured(AuthServiceError):
    """AUTH_SERVICE_URL / token missing, or URL is not https."""


class OTPRequired(AuthServiceError):
    """The auth-service needs an OTP / device approval to continue."""


def build_trailing_stop_payload(*, account_url, instrument_url, symbol, side,
                                quantity, trail_percent, stop_price=None,
                                time_in_force="gtc"):
    """Construct a percentage trailing-stop order payload (trailing_peg shape).

    Mirrors the auth-service's own builder so callers can pass friendly fields
    instead of a pre-assembled payload.
    """
    payload = {
        "account": account_url,
        "instrument": instrument_url,
        "symbol": symbol,
        "type": "market",
        "time_in_force": time_in_force,
        "trigger": "stop",
        "side": side,
        "quantity": str(quantity),
        "trailing_peg": {"type": "percentage", "percentage": str(trail_percent)},
        "ref_id": str(uuid.uuid4()),
    }
    if stop_price is not None:
        payload["stop_price"] = str(stop_price)
    return payload


def build_replace_payload(order, *, price=None, quantity=None):
    """Build an order-replace payload from a looked-up order dict.

    Robinhood amends an order by POSTing a full order body to
    ``/orders/{id}/replace/`` with a NEW ``ref_id`` — there is no HTTP PATCH.
    This copies the immutable fields off the existing order and overrides only
    price/quantity, minting a fresh ref_id so RH does not reject the change as
    a duplicate.

    Args:
        order: raw Robinhood order dict (from a lookup or a place response).
        price: new limit price; omitted keeps the order's current price.
        quantity: new quantity; omitted keeps the order's current quantity.

    Returns:
        A replace payload ready for ``replace_order``.
    """
    payload = {
        "account": order["account"],
        "instrument": order["instrument"],
        "type": order["type"],
        "side": order["side"],
        "time_in_force": order["time_in_force"],
        "trigger": order["trigger"],
        "quantity": str(quantity if quantity is not None else order["quantity"]),
        "ref_id": str(uuid.uuid4()),
    }
    if order.get("symbol"):
        payload["symbol"] = order["symbol"]
    new_price = price if price is not None else order.get("price")
    if new_price is not None:
        payload["price"] = str(new_price)
    if order.get("stop_price"):
        payload["stop_price"] = str(order["stop_price"])
    if order.get("trailing_peg"):
        payload["trailing_peg"] = order["trailing_peg"]
    return payload


def walk_order(client, order_id, target_price, *, step=0.05, dry_run=True,
               max_steps=25):
    """Walk a resting limit order's price toward a target via RH replaces.

    Momentum-style order chase: look the order up, replace it a step closer to
    ``target_price``, and repeat. Each RH replace returns a NEW order id, so we
    chain onto the replacement's id every step — reusing the original id would
    target an already-replaced order. Stops when the price reaches the target,
    the order is no longer editable (e.g. filled), or ``max_steps`` is hit.

    Live replaces are required to converge (a dry-run replace does not change
    state), so when ``dry_run`` is True this performs a single step and returns.

    Args:
        client: an AuthServiceClient (or compatible).
        order_id: id of the order to walk.
        target_price: price to walk toward.
        step: max price move per replace.
        dry_run: pass-through to replace; True does one step only.
        max_steps: safety bound on the number of replaces.

    Returns:
        List of per-step dicts: {from, to, order_id, result}.
    """
    results = []
    current_id = order_id
    for _ in range(max_steps):
        order = client.get_order(current_id)
        if not order.get("is_editable", True):
            log.info("walk_order: %s not editable (state=%s) — stopping",
                     current_id, order.get("state"))
            break
        cur = float(order.get("price") or 0)
        if abs(cur - target_price) < 1e-9:
            break
        nxt = (min(cur + step, target_price) if cur < target_price
               else max(cur - step, target_price))
        nxt = round(nxt, 2)
        payload = build_replace_payload(order, price=nxt)
        result = client.replace_order(current_id, payload, dry_run=dry_run)
        results.append({"from": cur, "to": nxt, "order_id": current_id,
                        "result": result})
        if dry_run:
            break
        new_id = result.get("id") if isinstance(result, dict) else None
        current_id = new_id or current_id
    return results


class AuthServiceClient:
    # (method, path) pairs allowed as direct exec against the auth-service.
    DIRECT_ALLOW_LIST = frozenset({
        ("GET", "/orders/trailing_stop"),
        ("POST", "/orders/trailing_stop"),
    })

    def __init__(self, base_url=None, token=None, timeout=None):
        self.base_url = (base_url if base_url is not None
                         else Config.AUTH_SERVICE_URL).rstrip("/")
        self.token = token if token is not None else Config.RH_AUTH_SERVICE_REQUEST_TOKEN
        self.timeout = timeout or Config.AUTH_SERVICE_TIMEOUT

    # -- internals --

    def _check_config(self):
        if not self.base_url:
            raise AuthServiceNotConfigured("AUTH_SERVICE_URL is not set")
        if not self.token:
            raise AuthServiceNotConfigured("RH_AUTH_SERVICE_REQUEST_TOKEN is not set")
        # Never send the bearer token over plaintext.
        if not self.base_url.startswith("https://"):
            raise AuthServiceNotConfigured(
                "AUTH_SERVICE_URL must be https — refusing to send the request "
                f"token over plaintext ({self.base_url})"
            )

    def _flag_otp(self, method, path, data):
        """Log ``OTP needed`` and raise if the response signals an OTP/approval."""
        status = str(data.get("status") or "").upper()
        code = str(data.get("error_code") or "").upper()
        detail = str(data.get("detail") or "")
        needs_otp = (
            status in _OTP_MARKERS
            or code in _OTP_MARKERS
            or "DEVICE APPROVAL" in detail.upper()
        )
        if needs_otp:
            log.warning("OTP needed — auth-service %s %s requires device "
                        "approval/OTP (status=%s code=%s detail=%s)",
                        method, path, status, code, detail)
            raise OTPRequired(detail or "OTP needed")

    def _request(self, method, path, json_body=None):
        self._check_config()
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            resp = requests.request(method, url, json=json_body,
                                    headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            log.error("auth-service %s %s failed: %s", method, path, e)
            raise AuthServiceError(f"auth-service request failed: {e}") from e

        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text}
        if not isinstance(data, dict):
            data = {"result": data}

        self._flag_otp(method, path, data)

        if resp.status_code >= 400:
            log.error("auth-service %s %s -> %s %s",
                      method, path, resp.status_code, data)
            raise AuthServiceError(
                f"auth-service {method} {path} returned {resp.status_code}: {data}"
            )
        return data

    # -- allow-listed direct calls --

    def get_trailing_stop_orders(self):
        return self._request("GET", "/orders/trailing_stop")

    def place_trailing_stop(self, payload, dry_run=True):
        return self._request("POST", "/orders/trailing_stop",
                             {"payload": payload, "dry_run": dry_run})

    # -- order lookup / patch / cancel --
    # replace_order uses the box's existing replace route; get_order and
    # cancel_order need matching box routes (auth-service task) to run live.

    def get_order(self, order_id):
        """Look up a single order by id (raw Robinhood order dict)."""
        return self._request("GET", f"/orders/{order_id}")

    def replace_order(self, order_id, payload, dry_run=True):
        """Patch an order by replacing it (RH POST /orders/{id}/replace/)."""
        return self._request("POST", "/orders/trailing_stop/replace",
                             {"order_id": order_id, "payload": payload,
                              "dry_run": dry_run})

    def cancel_order(self, order_id, dry_run=True):
        """Cancel a single order by id."""
        return self._request("POST", f"/orders/{order_id}/cancel",
                             {"dry_run": dry_run})

    # -- MCP passthrough (official Robinhood MCP, via POST /exec/mcp) --

    def mcp_relay(self, payload, session_id=None):
        """Relay a raw JSON-RPC 2.0 payload to the Robinhood MCP through the box.

        Returns the auth-service relay envelope: {ok, status, session_id,
        result|body, error_code?}. MCP-level failures come back as ok=false with
        an error_code (e.g. MCP_HTTP_401) rather than raising.
        """
        body = {"payload": payload}
        if session_id:
            body["session_id"] = session_id
        return self._request("POST", "/exec/mcp", body)

    def mcp_call(self, method, params=None, req_id=1, session_id=None):
        """Build a JSON-RPC 2.0 request and relay it to the Robinhood MCP."""
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method,
                   "params": params or {}}
        return self.mcp_relay(payload, session_id=session_id)

    # -- auth (exempt from the destructive-action gates) --

    def get_token(self):
        """Vend the box's live RH access token.

        Returns {token, token_type, expires_at, device_token, account_number}.
        Raises OTPRequired when the box is blocked on device approval/MFA,
        AuthServiceError otherwise (including 404 while the deployed box
        predates the /token endpoint).
        """
        return self._request("GET", "/token")

    def login(self):
        """Trigger a box-side (re)login — may block on device approval."""
        return self._request("POST", "/login")

    # -- status (does not raise on OTP; the OTP state IS the answer) --

    def auth_status(self):
        try:
            return self._request("GET", "/auth/status")
        except OTPRequired as e:
            return {"authenticated": False, "otp_needed": True, "detail": str(e)}
