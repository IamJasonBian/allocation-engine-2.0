"""/rh/* relay: path confinement and the write rules."""

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AUTH_SERVICE_ENV", "/nonexistent-env-for-tests")

import passthrough  # noqa: E402

BUY_OPEN = {"side": "buy", "position_effect": "open"}
SELL_OPEN = {"side": "sell", "position_effect": "open"}


def _check(path, **body):
    body.setdefault("ref_id", str(uuid.uuid4()))
    return passthrough.check_write("POST", path, body)


class UpstreamPathTests(unittest.TestCase):
    def test_maps_prefix_and_keeps_query(self):
        self.assertEqual(passthrough.upstream_path("/rh/options/orders/?state=open"),
                         "/options/orders/?state=open")

    def test_rejects_host_smuggling_and_traversal(self):
        for bad in ("/rh//evil.com/x", "/rh/../token", "/rh/https://evil.com/",
                    "/rhx/orders/", "/rh/orders/%2e%2e/"):
            self.assertIsNone(passthrough.upstream_path(bad), bad)


class WriteRuleTests(unittest.TestCase):
    def test_option_orders(self):
        o = "/options/orders/"
        self.assertIsNone(_check(o, quantity="1", price="6", legs=[BUY_OPEN, SELL_OPEN]))
        self.assertIsNone(_check(o, quantity="29", price="0.30",
                                 legs=[{"side": "sell", "position_effect": "close"}]))
        # Shape is Robinhood's job: fractional contracts / odd legs pass through.
        self.assertIsNone(_check(o, quantity="1.5", price="1", legs="not-a-list"))
        for legs in ([SELL_OPEN], [{"side": "SELL", "position_effect": "Open"}]):
            self.assertIn("naked", _check(o, quantity="1", price="1", legs=legs))
        # 29 x $1.30 x 100 = $3,770 > default $1,000
        self.assertIn("options_max_notional", _check(o, quantity="29", price="1.30"))
        # sell 2 / buy 1 per unit leaves one short uncovered
        self.assertIn("naked", _check(o, quantity="1", price="1", legs=[
            {**SELL_OPEN, "ratio_quantity": 2}, {**BUY_OPEN, "ratio_quantity": 1}]))
        self.assertIsNone(_check(o, quantity="1", price="1", legs=[
            {**SELL_OPEN, "ratio_quantity": 1}, {**BUY_OPEN, "ratio_quantity": 2}]))
        for junk in ("x", -1, 0, None):
            self.assertIn("ratio_quantity", _check(o, quantity="1", price="1", legs=[
                {**SELL_OPEN, "ratio_quantity": junk}, BUY_OPEN]), junk)

    def test_closing_orders_skip_the_cap(self):
        o = "/options/orders/"
        close = [{"side": "sell", "position_effect": "close", "ratio_quantity": 1},
                 {"side": "buy", "position_effect": "close", "ratio_quantity": 1}]
        # 1 x $20 x 100 = $2,000 > $1,000 cap, but it only reduces exposure
        self.assertIsNone(_check(o, quantity="1", price="20.00", legs=close))
        self.assertIn("ref_id", _check(o, quantity="1", price="20.00", legs=close, ref_id="x"))
        # a roll (close + open) is still capped
        roll = [close[0], BUY_OPEN]
        self.assertIn("options_max_notional", _check(o, quantity="1", price="20.00", legs=roll))
        # no legs is not "all closes"
        self.assertIn("options_max_notional", _check(o, quantity="29", price="1.30", legs=[]))

    def test_equity_orders(self):
        self.assertIsNone(_check("/orders/", quantity="2", price="158.25"))
        self.assertIn("equity_max_notional", _check("/orders/", quantity="100", price="158.25"))
        self.assertIn("ref_id", _check("/orders/", quantity="1", price="1", ref_id="nope"))

    def test_unparseable_cap_fields_fail_closed(self):
        for bad in ({"quantity": ["29"], "price": "1"}, {"quantity": "1", "price": "abc"},
                    {"price": "1"}):
            self.assertIn("needed for", _check("/options/orders/", **bad), bad)

    def test_write_allowlist(self):
        oid = str(uuid.uuid4())
        for path in (f"/orders/{oid}/cancel/", f"/options/orders/{oid}/cancel/"):
            self.assertIsNone(_check(path))
        for path in ("/ach/transfers/", "/accounts/TESTACCT/", "/options/orders/x/replace/"):
            self.assertIn("not an allowed write", _check(path))


if __name__ == "__main__":
    unittest.main()
