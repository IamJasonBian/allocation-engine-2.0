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

    # -- status (does not raise on OTP; the OTP state IS the answer) --

    def auth_status(self):
        try:
            return self._request("GET", "/auth/status")
        except OTPRequired as e:
            return {"authenticated": False, "otp_needed": True, "detail": str(e)}
