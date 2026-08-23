import json
import unittest
from unittest import mock

import mcp_client
import robinhood


class McpClientParseTests(unittest.TestCase):
    def test_parse_jsonrpc_from_sse_body(self):
        relay = {
            "ok": True,
            "body": (
                "event: message\n"
                'data: {"jsonrpc":"2.0","id":2,"result":{"tools":[]}}\n'
            ),
        }
        self.assertEqual(mcp_client.parse_jsonrpc_result(relay), {"tools": []})

    def test_unwrap_tool_content_parses_json_text(self):
        parsed = {"content": [{"type": "text", "text": '{"positions":[]}'}]}
        self.assertEqual(mcp_client.unwrap_tool_content(parsed), {"positions": []})


class GetBookTests(unittest.TestCase):
    def test_get_book_calls_portfolio_and_positions_tools(self):
        calls = []

        def fake_relay(payload, token=None, session_id=None, timeout=30):
            method = payload.get("method")
            if method == "initialize":
                return {"ok": True, "status": 200, "session_id": "sess-1",
                        "result": {"result": {"capabilities": {}}}}
            if method == "notifications/initialized":
                return {"ok": True, "status": 200, "session_id": "sess-1"}
            if method == "tools/call":
                name = payload["params"]["name"]
                calls.append(name)
                if name == "get_portfolio":
                    body = {"content": [{"type": "text", "text": '{"equity": 100}'}]}
                else:
                    body = {"content": [{"type": "text", "text": '[{"symbol":"AAPL"}]'}]}
                return {"ok": True, "status": 200, "session_id": "sess-1", "result": body}
            raise AssertionError(method)

        with mock.patch.object(mcp_client, "relay", side_effect=fake_relay):
            book = robinhood.get_book(mcp_token="jwt")
        self.assertEqual(book["source"], "mcp")
        self.assertEqual(book["portfolio"], {"equity": 100})
        self.assertEqual(book["positions"], [{"symbol": "AAPL"}])
        self.assertEqual(calls, ["get_portfolio", "get_positions"])


if __name__ == "__main__":
    unittest.main()
