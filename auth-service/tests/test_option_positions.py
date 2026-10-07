"""Unit tests for Robinhood option position normalization."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import robinhood  # noqa: E402


class OptionPositionNormalizeTest(unittest.TestCase):
    def test_normalize_long_call_lot(self):
        raw = {
            "quantity": "2",
            "type": "long",
            "chain_symbol": "MU",
            "average_price": "200.0000",
            "trade_value_multiplier": "100",
        }
        inst = {
            "type": "call",
            "strike_price": "95.0000",
            "expiration_date": "2026-03-20",
            "chain_symbol": "MU",
        }
        row = robinhood.normalize_option_position(raw, inst)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["purchase_price"], 2.0)
        self.assertNotIn("avg_price", row)
        self.assertEqual(row["entry_source"], "position_average_price")
        self.assertEqual(row["cost_basis"], 400.0)
        self.assertEqual(row["position_type"], "long")
        self.assertEqual(row["option_type"], "call")

    def test_fill_fallback_when_position_average_missing(self):
        raw = {
            "quantity": "1",
            "type": "long",
            "chain_symbol": "AAPL",
            "option": "https://api.robinhood.com/options/instruments/x/",
        }
        inst = {
            "type": "call",
            "strike_price": "150",
            "expiration_date": "2026-01-16",
            "chain_symbol": "AAPL",
        }
        row = robinhood.normalize_option_position(raw, inst, fill_entry_cents=350.0)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["purchase_price"], 3.5)
        self.assertEqual(row["entry_source"], "filled_open_order")

    def test_filled_open_order_price_scaled_to_per_contract(self):
        url = "https://api.robinhood.com/options/instruments/x/"
        orders = [{
            "state": "filled",
            "direction": "debit",
            "average_price": "2.00",  # per-share premium on option orders
            "legs": [{"side": "buy", "position_effect": "open", "option": url}],
        }]
        entries = robinhood._build_filled_open_entry_map(None, orders)
        self.assertEqual(entries[url], 200.0)

    def test_no_mark_fallback_without_rh_lot(self):
        raw = {"quantity": "1", "type": "long", "chain_symbol": "AAPL"}
        inst = {"type": "call", "strike_price": "1", "expiration_date": "2026-01-01",
                "chain_symbol": "AAPL"}
        self.assertIsNone(robinhood.normalize_option_position(raw, inst))

    def test_short_lot_still_normalized_for_read_api(self):
        raw = {
            "quantity": "1",
            "type": "short",
            "chain_symbol": "SPY",
            "average_price": "100",
            "trade_value_multiplier": "100",
        }
        inst = {
            "type": "put",
            "strike_price": "400",
            "expiration_date": "2026-06-19",
            "chain_symbol": "SPY",
        }
        row = robinhood.normalize_option_position(raw, inst)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["position_type"], "short")


if __name__ == "__main__":
    unittest.main()
