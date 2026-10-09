"""Unit tests for the trade-safety guardrails (no network, no server)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AUTH_SERVICE_ENV", "/nonexistent-env-for-tests")

import config  # noqa: E402
import guardrails  # noqa: E402
import robinhood  # noqa: E402


def _valid_trailing_payload(**overrides):
    payload = robinhood.build_trailing_stop_payload(
        account_url="https://api.robinhood.com/accounts/ACCT/",
        instrument_url="https://api.robinhood.com/instruments/abc/",
        symbol="TSLA",
        side="sell",
        quantity=10,
        trail_percent=5,
    )
    payload.update(overrides)
    return payload


class TrailingStopPayloadTests(unittest.TestCase):
    def test_canonical_builder_payload_passes(self):
        self.assertIsNone(guardrails.check_trailing_stop_payload(_valid_trailing_payload()))

    def test_buy_side_trailing_stop_passes(self):
        self.assertIsNone(guardrails.check_trailing_stop_payload(
            _valid_trailing_payload(side="buy")))

    def test_plain_market_buy_blocked(self):
        payload = {"account": "a", "instrument": "i", "symbol": "TSLA",
                   "type": "market", "trigger": "immediate", "side": "buy",
                   "quantity": "10", "time_in_force": "gfd"}
        self.assertIn("trigger", guardrails.check_trailing_stop_payload(payload))

    def test_limit_order_blocked(self):
        self.assertIn("price", guardrails.check_trailing_stop_payload(
            _valid_trailing_payload(price="100.00")))

    def test_missing_trailing_peg_blocked(self):
        payload = _valid_trailing_payload()
        del payload["trailing_peg"]
        self.assertIn("trailing_peg", guardrails.check_trailing_stop_payload(payload))

    def test_price_peg_blocked(self):
        self.assertIn("percentage", guardrails.check_trailing_stop_payload(
            _valid_trailing_payload(trailing_peg={"type": "price", "price": "5.00"})))

    def test_non_market_type_blocked(self):
        self.assertIn("market", guardrails.check_trailing_stop_payload(
            _valid_trailing_payload(type="limit")))

    def test_bad_side_blocked(self):
        self.assertIn("side", guardrails.check_trailing_stop_payload(
            _valid_trailing_payload(side="hold")))

    def test_zero_quantity_blocked(self):
        self.assertIn("quantity", guardrails.check_trailing_stop_payload(
            _valid_trailing_payload(quantity="0")))

    def test_negative_quantity_blocked(self):
        self.assertIn("quantity", guardrails.check_trailing_stop_payload(
            _valid_trailing_payload(quantity="-5")))

    def test_out_of_range_percentage_blocked(self):
        for pct in ("0", "150", "junk"):
            self.assertIn("percentage", guardrails.check_trailing_stop_payload(
                _valid_trailing_payload(
                    trailing_peg={"type": "percentage", "percentage": pct})),
                msg=f"pct={pct}")


def _option_limit_order(**overrides):
    order = {
        "contract": {
            "chain_symbol": "MU", "option_type": "call", "strike": 95,
            "expiration": "2026-03-20",
        },
        "action": "buy_to_open",
        "quantity": 1, "limit_price": "2.50", "time_in_force": "gtc",
        "ref_id": "6a4698d9-a3c4-4621-a699-7b6eafe5bb14",
    }
    if overrides:
        contract_over = {k: v for k, v in overrides.items()
                         if k in ("chain_symbol", "option_type", "strike", "expiration")}
        top = {k: v for k, v in overrides.items() if k not in contract_over}
        if contract_over:
            order["contract"] = {**order["contract"], **contract_over}
        order.update(top)
    return order


class OptionLimitOrderTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(config, "OPTIONS_LIMIT_MAX_NOTIONAL", 1000.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_valid_orders_pass(self):
        cases = [
            _option_limit_order(),
            _option_limit_order(action="sell_to_open"),
            _option_limit_order(limit_price="0.1234"),
            _option_limit_order(option_type="put"),
        ]
        for order in cases:
            self.assertIsNone(guardrails.check_option_limit_order(order), msg=order)

    def test_invalid_orders_blocked(self):
        cases = [
            ({"chain_symbol": None}, "chain_symbol"),
            ({"chain_symbol": "  "}, "chain_symbol"),
            ({"option_type": "straddle"}, "option_type"),
            ({"expiration": "03/20/2026"}, "expiration"),
            ({"strike": 0}, "strike"),
            ({"action": "buy"}, "action"),
            ({"quantity": 1.5}, "contracts"), ({"quantity": 0}, "contracts"),
            ({"quantity": "NaN"}, "finite"),
            ({"limit_price": 0}, "> 0"), ({"limit_price": "2.555"}, "decimal places"),
            ({"limit_price": "0.12345"}, "decimal places"),
            ({"quantity": 5}, "max_notional"),
            ({"time_in_force": "ioc"}, "time_in_force"),
            ({"ref_id": "abc"}, "ref_id"),
        ]
        for overrides, expected in cases:
            reason = guardrails.check_option_limit_order(_option_limit_order(**overrides))
            self.assertIn(expected, reason or "", msg=overrides)


class McpPayloadTests(unittest.TestCase):
    def _call(self, tool, arguments=None):
        return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}}}

    def test_non_tool_call_methods_pass(self):
        for method in ("initialize", "tools/list", "ping", "resources/list",
                       "prompts/list", "notifications/initialized"):
            self.assertIsNone(guardrails.check_mcp_payload(
                {"jsonrpc": "2.0", "id": 1, "method": method}), msg=method)

    def test_read_tools_pass(self):
        for tool in ("get_orders", "list_positions", "get_portfolio",
                     "get_quote", "search_instruments", "order_history"):
            self.assertIsNone(guardrails.check_mcp_payload(self._call(tool)), msg=tool)

    def test_destructive_tools_blocked(self):
        for tool in ("buy_stock", "sell_stock", "place_order", "submit_order",
                     "create_order", "cancel_order", "modify_order",
                     "close_position", "transfer_funds", "withdraw_cash",
                     "execute_trade", "sell", "buy"):
            reason = guardrails.check_mcp_payload(self._call(tool))
            self.assertIsNotNone(reason, msg=tool)
            self.assertIn("destructive", reason)

    def test_trailing_stop_tools_exempt(self):
        for tool in ("place_trailing_stop_order", "replace_trailing_stop",
                     "cancel_trailing_stop_order", "update_trailing_stop"):
            self.assertIsNone(guardrails.check_mcp_payload(self._call(tool)), msg=tool)

    def test_allow_list_overrides_block(self):
        original = config.MCP_ALLOWED_TOOLS
        config.MCP_ALLOWED_TOOLS = "cancel_order, rebalance_portfolio"
        try:
            self.assertIsNone(guardrails.check_mcp_payload(self._call("cancel_order")))
            self.assertIsNotNone(guardrails.check_mcp_payload(self._call("buy_stock")))
        finally:
            config.MCP_ALLOWED_TOOLS = original

    def test_tool_call_without_name_blocked(self):
        self.assertIsNotNone(guardrails.check_mcp_payload(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}))


class ReadRouteTests(unittest.TestCase):
    def test_option_positions_read_is_allowlisted(self):
        self.assertIn("/positions/options", guardrails.ALLOWED_READ_ROUTES)


class ExecCommandTests(unittest.TestCase):
    def test_benign_commands_pass(self):
        for cmd in ("echo hi", "uptime", ["ls", "-la", "/tmp"],
                    "curl -s https://example.com/health"):
            self.assertIsNone(guardrails.check_exec_command(cmd), msg=str(cmd))

    def test_rh_order_urls_blocked_string_and_list(self):
        self.assertIsNotNone(guardrails.check_exec_command(
            "curl -X POST https://api.robinhood.com/orders/ -d '{}'"))
        self.assertIsNotNone(guardrails.check_exec_command(
            ["curl", "https://api.robinhood.com/options/orders/"]))

    def test_money_movement_urls_blocked(self):
        for url in ("https://api.robinhood.com/transfers/",
                    "https://api.robinhood.com/ach/relationships/",
                    "https://api.robinhood.com/payments/"):
            self.assertIsNotNone(guardrails.check_exec_command(f"curl {url}"), msg=url)

    def test_raw_mcp_url_blocked(self):
        self.assertIsNotNone(guardrails.check_exec_command(
            "curl https://agent.robinhood.com/mcp/trading"))


if __name__ == "__main__":
    unittest.main()
