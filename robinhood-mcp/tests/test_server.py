"""Tests for the local Robinhood MCP box — protocol + trade-safety (no network)."""

import json
import os
import subprocess
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402

DESTRUCTIVE_FRAGMENTS = ("buy", "sell", "cancel", "transfer", "withdraw",
                         "deposit", "execute", "submit", "close")


class FakeHttp:
    """Stub for server.http_json — canned responses keyed by (method, url prefix)."""

    def __init__(self):
        self.calls = []
        self.routes = []

    def route(self, method, url_part, response):
        self.routes.append((method, url_part, response))

    def __call__(self, method, url, body=None, headers=None, timeout=30):
        call = {"method": method, "url": url, "body": body,
                "headers": headers or {}}
        self.calls.append(call)
        for m, part, resp in self.routes:
            if m == method and part in url:
                return resp(call) if callable(resp) else resp
        raise AssertionError(f"unexpected {method} {url}")


class ProtocolTests(unittest.TestCase):
    def test_initialize(self):
        resp = server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(resp["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(resp["result"]["serverInfo"]["name"], "robinhood-local")

    def test_notifications_get_no_response(self):
        self.assertIsNone(server.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_unknown_method_errors(self):
        resp = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "bogus"})
        self.assertEqual(resp["error"]["code"], -32601)

    def test_tools_list_has_no_destructive_tools(self):
        resp = server.handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertEqual(sorted(names), sorted(server.TOOLS))
        for name in names:
            if "trailing" in name:  # the sanctioned write
                continue
            for frag in DESTRUCTIVE_FRAGMENTS:
                self.assertNotIn(frag, name, msg=name)

    def test_unknown_tool_call_errors(self):
        resp = server.handle_message(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "buy_stock", "arguments": {"symbol": "TSLA"}}})
        self.assertIn("unknown tool", resp["error"]["message"])

    def test_bad_arguments_become_tool_error_not_crash(self):
        resp = server.handle_message(
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "get_stock_orders", "arguments": {"nope": 1}}})
        self.assertTrue(resp["result"]["isError"])


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.http = FakeHttp()
        self._orig = server.http_json
        server.http_json = self.http
        server._token_cache["token"] = "test-token"
        server._symbol_cache.clear()
        server._account_cache.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        server.http_json = self._orig
        server._token_cache["token"] = None

    def test_get_stock_orders_resolves_symbols(self):
        self.http.route("GET", "/orders/", {
            "results": [{"id": "o1", "state": "filled",
                         "instrument": "https://api.robinhood.com/instruments/i1/"}],
            "next": None})
        self.http.route("GET", "/instruments/i1/", {"symbol": "TSLA"})
        out = server.tool_get_stock_orders(limit=10)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["orders"][0]["symbol"], "TSLA")

    def test_trailing_stop_filter(self):
        self.http.route("GET", "/orders/", {"results": [
            {"id": "t1", "state": "confirmed", "trigger": "stop",
             "trailing_peg": {"type": "percentage", "percentage": "5"},
             "instrument": ""},
            {"id": "x1", "state": "confirmed", "trigger": "immediate", "instrument": ""},
            {"id": "x2", "state": "filled", "trigger": "stop", "instrument": ""},
        ], "next": None})
        out = server.tool_get_trailing_stop_orders()
        self.assertEqual([o["id"] for o in out["orders"]], ["t1"])

    def _route_instrument_and_account(self):
        self.http.route("GET", "/instruments/?symbol=", {
            "results": [{"url": "https://api.robinhood.com/instruments/i1/",
                         "symbol": "TSLA"}]})
        self.http.route("GET", "/accounts/", {
            "results": [{"url": "https://api.robinhood.com/accounts/A1/",
                         "account_number": "A1"}]})

    def test_place_trailing_stop_dry_run_default_and_shape(self):
        self._route_instrument_and_account()
        out = server.tool_place_trailing_stop("TSLA", "sell", 10, 5)
        self.assertTrue(out["dry_run"])
        p = out["payload"]
        self.assertEqual(p["trigger"], "stop")
        self.assertEqual(p["trailing_peg"], {"type": "percentage", "percentage": "5"})
        self.assertEqual(p["type"], "market")
        self.assertNotIn("price", p)
        # dry run must never POST to RH
        self.assertFalse([c for c in self.http.calls if c["method"] == "POST"])

    def test_place_live_posts_to_orders(self):
        self._route_instrument_and_account()
        self.http.route("POST", "/orders/", {"id": "new-1"})
        out = server.tool_place_trailing_stop("TSLA", "sell", 10, 5, dry_run=False)
        self.assertEqual(out["id"], "new-1")
        posts = [c for c in self.http.calls if c["method"] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["body"]["trigger"], "stop")

    def test_place_validation(self):
        for kwargs in ({"side": "hold"}, {"quantity": 0}, {"trail_percent": 0},
                       {"trail_percent": 200}):
            args = {"symbol": "TSLA", "side": "sell", "quantity": 10,
                    "trail_percent": 5, **kwargs}
            with self.assertRaises(ValueError, msg=str(kwargs)):
                server.tool_place_trailing_stop(**args)

    def test_replace_targets_replace_url(self):
        self._route_instrument_and_account()
        out = server.tool_replace_trailing_stop("ord-9", "TSLA", "sell", 10, 5)
        self.assertTrue(out["dry_run"])
        self.assertIn("/orders/ord-9/replace/", out["url"])

    def test_sync_trading_db_batches(self):
        stock = [{"id": f"s{i}", "state": "filled", "instrument": ""}
                 for i in range(150)]
        # options route first: routes match in order and "/orders/" is a
        # substring of "/options/orders/"
        self.http.route("GET", "/options/orders/", {"results": [], "next": None})
        self.http.route("GET", "/orders/", {"results": stock, "next": None})
        self.http.route("POST", "/db-orders", lambda call: {
            "data": {"stock_upserted": len(call["body"].get("orders", [])),
                     "option_upserted": len(call["body"].get("option_orders", []))}})
        out = server.tool_sync_trading_db()
        self.assertEqual(out["pulled"]["stock"], 150)
        self.assertEqual(out["upserted"]["stock"], 150)
        posts = [c for c in self.http.calls if "/db-orders" in c["url"]]
        self.assertEqual(len(posts), 2)  # 150 orders -> two batches of <=100


class BoxAuthTests(unittest.TestCase):
    """All RH auth must flow through the auth-service box's /token."""

    def setUp(self):
        self.http = FakeHttp()
        self._orig = server.http_json
        server.http_json = self.http
        server._token_cache["token"] = None
        self.env = unittest.mock.patch.dict(os.environ, {
            "AUTH_SERVICE_URL": "https://box.test",
            "AUTH_SERVICE_TOKEN": "exec-bearer"})
        self.env.start()
        self.addCleanup(self._restore)

    def _restore(self):
        server.http_json = self._orig
        server._token_cache["token"] = None
        self.env.stop()

    def test_token_vended_from_box_and_used_on_rh(self):
        self.http.route("GET", "box.test/token", {"token": "vended-1"})
        self.http.route("GET", "/orders/", {"results": [], "next": None})
        server.tool_get_trailing_stop_orders()
        vend = self.http.calls[0]
        self.assertIn("box.test/token", vend["url"])
        self.assertEqual(vend["headers"]["Authorization"], "Bearer exec-bearer")
        rh = self.http.calls[1]
        self.assertEqual(rh["headers"]["Authorization"], "Bearer vended-1")

    def test_rh_401_revends_once_and_retries(self):
        tokens = iter(["stale", "fresh"])
        self.http.route("GET", "box.test/token", lambda call: {"token": next(tokens)})

        def orders(call):
            if call["headers"]["Authorization"] == "Bearer stale":
                raise server.HttpError(401, "HTTP 401 from rh")
            return {"results": [], "next": None}
        self.http.route("GET", "/orders/", orders)
        out = server.tool_get_trailing_stop_orders()
        self.assertEqual(out["count"], 0)
        vends = [c for c in self.http.calls if "box.test/token" in c["url"]]
        self.assertEqual(len(vends), 2)  # initial vend + forced re-vend

    def test_non_401_error_is_not_retried(self):
        self.http.route("GET", "box.test/token", {"token": "t1"})

        def orders(call):
            raise server.HttpError(503, "HTTP 503 from rh")
        self.http.route("GET", "/orders/", orders)
        with self.assertRaises(server.HttpError):
            server.tool_get_trailing_stop_orders()
        vends = [c for c in self.http.calls if "box.test/token" in c["url"]]
        self.assertEqual(len(vends), 1)

    def test_missing_env_is_a_clear_error(self):
        self.env.stop()
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTH_SERVICE_URL", None)
            os.environ.pop("AUTH_SERVICE_TOKEN", None)
            with self.assertRaises(RuntimeError) as ctx:
                server.rh_token()
        self.env.start()  # so _restore's stop() stays balanced
        self.assertIn("AUTH_SERVICE_URL", str(ctx.exception))


class StdioSmokeTest(unittest.TestCase):
    def test_initialize_and_list_over_stdio(self):
        lines = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]) + "\n"
        proc = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "..", "server.py")],
            input=lines, capture_output=True, text=True, timeout=30)
        responses = [json.loads(l) for l in proc.stdout.strip().splitlines()]
        self.assertEqual(len(responses), 2)  # notification produced no response
        self.assertEqual(responses[0]["id"], 1)
        self.assertIn("tools", responses[1]["result"])


if __name__ == "__main__":
    unittest.main()
