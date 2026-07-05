"""HTTP-level tests: real server on an ephemeral port, mocked RH/MCP layers.

Verifies the two properties callers depend on:
  1. Auth interface — every privileged endpoint rejects missing/wrong/malformed
     bearer tokens (401) and accepts the configured one; /health stays open.
  2. Guardrails — destructive payloads are refused (403) before any relay,
     while trailing-stop traffic and reads pass through.
"""

import http.client
import json
import os
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AUTH_SERVICE_ENV", "/nonexistent-env-for-tests")

import config  # noqa: E402
import robinhood  # noqa: E402
import server  # noqa: E402
from models import AuthResult, Session  # noqa: E402

TOKEN = "test-exec-token"

FAKE_SESSION = Session(
    access_token="fake-access-token",
    refresh_token="fake-refresh",
    token_type="Bearer",
    expires_at=time.time() + 3600,
    device_token="fake-device",
    account_url="https://api.robinhood.com/accounts/TESTACCT/",
    account_number="TESTACCT",
)

PRIVILEGED = [
    ("GET", "/orders/trailing_stop"),
    ("GET", "/token"),
    ("POST", "/login"),
    ("POST", "/orders/trailing_stop"),
    ("POST", "/orders/trailing_stop/replace"),
    ("POST", "/command"),
    ("POST", "/exec"),
    ("POST", "/exec/mcp"),
]


def _valid_trailing_payload(**overrides):
    payload = robinhood.build_trailing_stop_payload(
        account_url="https://api.robinhood.com/accounts/TESTACCT/",
        instrument_url="https://api.robinhood.com/instruments/abc/",
        symbol="TSLA",
        side="sell",
        quantity=10,
        trail_percent=5,
    )
    payload.update(overrides)
    return payload


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.EXEC_TOKEN = TOKEN
        config.EXEC_TOKEN_SECRET = ""
        config.MCP_TOKEN = ""
        config.MCP_TOKEN_SECRET = ""
        config.MCP_ALLOWED_TOOLS = ""
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        # Authenticated-session happy path by default; tests override as needed.
        self.get_session = mock.patch.object(
            server.session_mgr, "get_session",
            return_value=AuthResult("OK", session=FAKE_SESSION)).start()
        self.status = mock.patch.object(
            server.session_mgr, "status",
            return_value={"profile": "rh.auth", "authenticated": True,
                          "expires_at": FAKE_SESSION.expires_at,
                          "account_number": "TESTACCT"}).start()
        self.relay = mock.patch.object(
            server.mcp_client, "relay",
            return_value={"ok": True, "status": 200, "result": {}}).start()
        self.run_command = mock.patch.object(
            server, "run_command",
            return_value={"exit_code": 0, "stdout": "", "stderr": "",
                          "timed_out": False}).start()
        self.addCleanup(mock.patch.stopall)

    def request(self, method, path, body=None, token=TOKEN, raw_header=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if raw_header is not None:
            headers["Authorization"] = raw_header
        elif token is not None:
            headers["Authorization"] = f"Bearer {token}"
        payload = None
        if body is not None:
            payload = body if isinstance(body, (bytes, str)) else json.dumps(body)
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = {"_raw": data.decode("utf-8", "replace")}
        return resp.status, parsed

    # ---- auth interface ----

    def test_health_needs_no_auth(self):
        status, body = self.request("GET", "/health", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_auth_status_needs_no_auth(self):
        status, body = self.request("GET", "/auth/status", token=None)
        self.assertEqual(status, 200)
        self.assertTrue(body["authenticated"])

    def test_privileged_endpoints_reject_missing_token(self):
        for method, path in PRIVILEGED:
            status, _ = self.request(method, path, body={}, token=None)
            self.assertEqual(status, 401, msg=f"{method} {path}")

    def test_privileged_endpoints_reject_wrong_token(self):
        for method, path in PRIVILEGED:
            status, _ = self.request(method, path, body={}, token="wrong-token")
            self.assertEqual(status, 401, msg=f"{method} {path}")

    def test_privileged_endpoints_reject_malformed_headers(self):
        for raw in (f"Token {TOKEN}", f"bearer {TOKEN}", "Bearer", "Bearer ", TOKEN):
            status, _ = self.request("GET", "/token", raw_header=raw)
            self.assertEqual(status, 401, msg=repr(raw))

    def test_unconfigured_exec_token_refuses_all(self):
        with mock.patch.object(config, "EXEC_TOKEN", ""):
            status, _ = self.request("GET", "/token", token="")
            self.assertEqual(status, 401)
            status, _ = self.request("GET", "/token", token=TOKEN)
            self.assertEqual(status, 401)

    def test_correct_token_accepted_on_reads(self):
        with mock.patch.object(server.robinhood, "get_trailing_stop_orders",
                               return_value=[]):
            status, body = self.request("GET", "/orders/trailing_stop")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"count": 0, "orders": []})

    def test_token_vend_returns_session_token(self):
        status, body = self.request("GET", "/token")
        self.assertEqual(status, 200)
        self.assertEqual(body["token"], "fake-access-token")
        self.assertEqual(body["account_number"], "TESTACCT")

    def test_token_vend_conflict_when_unauthenticated(self):
        self.get_session.return_value = AuthResult(
            "ERROR", error_code="LOGIN_NO_TOKEN")
        status, body = self.request("GET", "/token")
        self.assertEqual(status, 409)
        self.assertEqual(body["error_code"], "LOGIN_NO_TOKEN")

    def test_login_forces_reauth(self):
        status, _ = self.request("POST", "/login", body={})
        self.assertEqual(status, 200)
        self.get_session.assert_called_with(config.DEFAULT_PROFILE, force=True)

    def test_unknown_route_404(self):
        status, _ = self.request("GET", "/nope")
        self.assertEqual(status, 404)

    def test_malformed_json_400(self):
        status, _ = self.request("POST", "/orders/trailing_stop", body="{not json")
        self.assertEqual(status, 400)

    # ---- trailing-stop guardrails over HTTP ----

    def test_place_valid_trailing_stop_dry_run_default(self):
        status, body = self.request("POST", "/orders/trailing_stop",
                                    body={"payload": _valid_trailing_payload()})
        self.assertEqual(status, 200)
        self.assertTrue(body["dry_run"])  # dry_run must default to true

    def test_place_live_passes_dry_run_false_through(self):
        with mock.patch.object(server.robinhood, "place_trailing_stop",
                               return_value={"id": "ord-1"}) as place:
            status, _ = self.request(
                "POST", "/orders/trailing_stop",
                body={"payload": _valid_trailing_payload(), "dry_run": False})
        self.assertEqual(status, 200)
        self.assertFalse(place.call_args.kwargs["dry_run"])

    def test_place_plain_market_buy_blocked(self):
        payload = {"account": "a", "instrument": "i", "symbol": "TSLA",
                   "type": "market", "trigger": "immediate", "side": "buy",
                   "quantity": "10"}
        status, body = self.request("POST", "/orders/trailing_stop",
                                    body={"payload": payload, "dry_run": False})
        self.assertEqual(status, 403)
        self.assertEqual(body["error_code"], "GUARDRAIL_BLOCKED")

    def test_place_limit_order_blocked(self):
        status, body = self.request(
            "POST", "/orders/trailing_stop",
            body={"payload": _valid_trailing_payload(price="1.00")})
        self.assertEqual(status, 403)
        self.assertEqual(body["error_code"], "GUARDRAIL_BLOCKED")

    def test_replace_guarded_too(self):
        payload = _valid_trailing_payload(trigger="immediate")
        status, body = self.request(
            "POST", "/orders/trailing_stop/replace",
            body={"order_id": "ord-1", "payload": payload})
        self.assertEqual(status, 403)
        self.assertEqual(body["error_code"], "GUARDRAIL_BLOCKED")

    def test_replace_valid_passes(self):
        status, body = self.request(
            "POST", "/orders/trailing_stop/replace",
            body={"order_id": "ord-1", "payload": _valid_trailing_payload()})
        self.assertEqual(status, 200)
        self.assertTrue(body["dry_run"])

    # ---- MCP guardrails over HTTP ----

    def test_mcp_tools_list_relayed_with_session_fallback_token(self):
        status, body = self.request(
            "POST", "/exec/mcp",
            body={"payload": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        # MCP_TOKEN unset -> the live session bearer is attached instead.
        self.assertEqual(self.relay.call_args.kwargs["token"], "fake-access-token")

    def test_mcp_read_tool_call_relayed(self):
        status, _ = self.request(
            "POST", "/exec/mcp",
            body={"method": "tools/call", "id": 2,
                  "params": {"name": "get_portfolio", "arguments": {}}})
        self.assertEqual(status, 200)
        self.relay.assert_called_once()

    def test_mcp_destructive_tool_blocked_before_relay(self):
        for tool in ("buy_stock", "sell_stock", "place_order", "cancel_order"):
            self.relay.reset_mock()
            status, body = self.request(
                "POST", "/exec/mcp",
                body={"method": "tools/call", "id": 3,
                      "params": {"name": tool, "arguments": {"symbol": "TSLA"}}})
            self.assertEqual(status, 403, msg=tool)
            self.assertEqual(body["error_code"], "GUARDRAIL_BLOCKED", msg=tool)
            self.relay.assert_not_called()

    def test_mcp_trailing_stop_tool_allowed(self):
        status, _ = self.request(
            "POST", "/exec/mcp",
            body={"method": "tools/call", "id": 4,
                  "params": {"name": "place_trailing_stop_order", "arguments": {}}})
        self.assertEqual(status, 200)
        self.relay.assert_called_once()

    # ---- /exec guardrails over HTTP ----

    def test_exec_benign_command_runs(self):
        status, body = self.request("POST", "/exec", body={"command": "echo hi"})
        self.assertEqual(status, 200)
        self.assertEqual(body["exit_code"], 0)
        self.run_command.assert_called_once()

    def test_exec_rh_order_curl_blocked_before_run(self):
        status, body = self.request(
            "POST", "/exec",
            body={"command": "curl -X POST https://api.robinhood.com/orders/"})
        self.assertEqual(status, 403)
        self.assertEqual(body["error_code"], "GUARDRAIL_BLOCKED")
        self.run_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
