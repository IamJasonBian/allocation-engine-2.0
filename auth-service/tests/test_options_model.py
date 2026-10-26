"""Tests for options_model — contract/action intent shape."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import options_model


class FlattenRuntimeOrderTests(unittest.TestCase):
    def test_wraps_flat_engine_fields(self):
        flat = {
            "chain_symbol": "mu", "option_type": "call", "strike": 95,
            "expiration": "2026-03-20", "side": "buy", "position_effect": "open",
            "quantity": 2, "limit_price": "1.25",
            "ref_id": "6a4698d9-a3c4-4621-a699-7b6eafe5bb14",
        }
        out = options_model.flatten_runtime_order(flat)
        self.assertEqual(out["action"], "buy_to_open")
        self.assertEqual(out["contract"]["chain_symbol"], "mu")
        self.assertEqual(out["quantity"], 2)

    def test_passes_through_nested_contract(self):
        nested = _option_limit_order()
        self.assertIs(options_model.flatten_runtime_order(nested), nested)


def _option_limit_order():
    return {
        "contract": {"chain_symbol": "MU", "option_type": "call", "strike": 95,
                     "expiration": "2026-03-20"},
        "action": "buy_to_open",
        "quantity": 1, "limit_price": "2.50",
        "ref_id": "6a4698d9-a3c4-4621-a699-7b6eafe5bb14",
    }


class ActionMappingTests(unittest.TestCase):
    def test_all_actions_map_to_rh_leg_fields(self):
        for action in options_model.OPTION_ACTIONS:
            leg = options_model.rh_leg_from_action(action)
            self.assertEqual(set(leg), {"side", "position_effect", "direction"})


if __name__ == "__main__":
    unittest.main()
