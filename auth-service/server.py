#!/usr/bin/env python3
"""Lightweight auth-service HTTP server (stdlib only, no Flask).

Other services call this box to (a) confirm we're authenticated and (b) act on
percentage trailing-stop orders inside the authenticated Robinhood session.

Endpoints (all POSTs and order reads require Bearer EXEC_TOKEN):
  GET  /health                        — liveness
  GET  /auth/status                   — auth state + error codes (for alerting)
  POST /login                         — trigger the login flow (device approval)
  GET  /orders/trailing_stop          — active percentage trailing-stop orders
  POST /orders/trailing_stop          — place one (dry_run defaults to true)
  POST /orders/trailing_stop/replace  — replace one (dry_run defaults to true)
  POST /exec                          — run an external command

Threaded server; single process; tiny footprint (e2-micro friendly).
"""

import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import mcp_client
import robinhood
import session as session_mgr
from gcp_secrets import get_secret
from runner import run_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("server")


def _notify_approval(profile_id: str):
    log.warning("*** DEVICE APPROVAL NEEDED for %s — approve the push on your phone ***",
                profile_id)


session_mgr.on_approval_needed = _notify_approval


def _exec_token() -> str:
    if config.EXEC_TOKEN_SECRET:
        return get_secret(config.EXEC_TOKEN_SECRET, config.GCP_PROJECT_ID)
    return config.EXEC_TOKEN


def _mcp_token() -> str:
    if config.MCP_TOKEN_SECRET:
        return get_secret(config.MCP_TOKEN_SECRET, config.GCP_PROJECT_ID)
    return config.MCP_TOKEN


def _ensure_session(profile_id: str):
    """Return (session, error_response|None). Relays error codes to the caller."""
    result = session_mgr.get_session(profile_id)
    if result.status == "OK" and result.session is not None:
        return result.session, None
    return None, {
        "authenticated": False,
        "status": result.status,
        "error_code": result.error_code,
        "detail": result.detail,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "auth-service/1.0"

    # -- helpers --

    def _send(self, code: int, body: dict):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def _authorized(self) -> bool:
        expected = _exec_token()
        if not expected:
            log.warning("EXEC_TOKEN not configured — refusing privileged call")
            return False
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        return hmac.compare_digest(header[len(prefix):], expected)

    def _profile(self, body: dict | None = None) -> str:
        if body and body.get("profile"):
            return body["profile"]
        return config.DEFAULT_PROFILE

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    # -- routing --

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "service": "auth-service"})
        elif self.path == "/auth/status":
            self._send(200, session_mgr.status(config.DEFAULT_PROFILE))
        elif self.path.rstrip("/") == "/orders/trailing_stop":
            self._handle_read_orders()
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        route = self.path.rstrip("/")
        if route == "/login":
            self._handle_login()
        elif route == "/orders/trailing_stop":
            self._handle_place()
        elif route == "/orders/trailing_stop/replace":
            self._handle_replace()
        elif route == "/command":
            self._handle_command()
        elif route == "/exec/mcp":
            self._handle_mcp()
        elif route == "/exec":
            self._handle_exec()
        else:
            self._send(404, {"error": "not found"})

    # -- handlers --

    def _handle_login(self):
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        result = session_mgr.get_session(config.DEFAULT_PROFILE, force=True)
        code = 200 if result.status == "OK" else 502
        self._send(code, {
            "status": result.status,
            "error_code": result.error_code,
            "detail": result.detail,
            **session_mgr.status(config.DEFAULT_PROFILE),
        })

    def _handle_read_orders(self):
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        sess, err = _ensure_session(config.DEFAULT_PROFILE)
        if err:
            self._send(409, err)
            return
        try:
            orders = robinhood.get_trailing_stop_orders(sess)
            self._send(200, {"count": len(orders), "orders": orders})
        except Exception as e:  # noqa: BLE001
            log.exception("read orders failed")
            self._send(502, {"error_code": "READ_ORDERS_FAILED", "detail": str(e)})

    def _handle_place(self):
        # The order payload is built elsewhere; we authenticate, relay it to
        # Robinhood, and return the resulting status/codes. dry_run defaults true.
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON body"})
            return
        payload = body.get("payload")
        if not isinstance(payload, dict):
            self._send(400, {"error": "missing 'payload' object"})
            return
        sess, err = _ensure_session(self._profile(body))
        if err:
            self._send(409, err)
            return
        try:
            result = robinhood.place_trailing_stop(
                sess, payload, dry_run=body.get("dry_run", True))
            self._send(200, result)
        except Exception as e:  # noqa: BLE001
            log.exception("place failed")
            self._send(502, {"error_code": "PLACE_FAILED", "detail": str(e)})

    def _handle_replace(self):
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON body"})
            return
        payload = body.get("payload")
        if not isinstance(payload, dict) or not body.get("order_id"):
            self._send(400, {"error": "need 'order_id' and 'payload' object"})
            return
        sess, err = _ensure_session(self._profile(body))
        if err:
            self._send(409, err)
            return
        try:
            result = robinhood.replace_trailing_stop(
                sess, body["order_id"], payload, dry_run=body.get("dry_run", True))
            self._send(200, result)
        except Exception as e:  # noqa: BLE001
            log.exception("replace failed")
            self._send(502, {"error_code": "REPLACE_FAILED", "detail": str(e)})

    def _handle_command(self):
        # Generic authenticated intake for other services. For now we validate
        # the caller + confirm an authenticated session and relay the result
        # codes; real per-action dispatch is added later.
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON body"})
            return
        action = body.get("action")
        if not action:
            self._send(400, {"error": "missing 'action'"})
            return
        sess, err = _ensure_session(self._profile(body))
        if err:
            self._send(409, err)
            return
        self._send(200, {
            "accepted": True,
            "action": action,
            "authenticated": True,
            "account_number": sess.account_number,
        })

    def _handle_mcp(self):
        # Relay a JSON-RPC call to the official Robinhood MCP — the "mcp exec"
        # option alongside the normal shell /exec. Caller authenticates with our
        # bearer token; the MCP's OAuth token is attached server-side. Does not
        # need our RH password session (the MCP has its own OAuth).
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON body"})
            return
        payload = body.get("payload")
        if payload is None and body.get("method"):
            payload = body  # caller sent the JSON-RPC object directly
        if not isinstance(payload, dict):
            self._send(400, {"error": "missing JSON-RPC 'payload'"})
            return
        result = mcp_client.relay(payload, token=_mcp_token(),
                                  session_id=body.get("session_id"))
        self._send(200, result)

    def _handle_exec(self):
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        if config.EXEC_REQUIRE_AUTH:
            _, err = _ensure_session(config.DEFAULT_PROFILE)
            if err:
                self._send(409, err)
                return
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON body"})
            return
        command = body.get("command")
        if not command:
            self._send(400, {"error": "missing 'command'"})
            return
        try:
            self._send(200, run_command(command, cwd=body.get("cwd")))
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("exec failed")
            self._send(500, {"error": str(e)})


def main():
    server = ThreadingHTTPServer(("0.0.0.0", config.PORT), Handler)
    log.info("auth-service listening on :%d", config.PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
