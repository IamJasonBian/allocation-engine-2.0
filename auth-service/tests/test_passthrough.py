"""/rh/* relay: reads pass untouched, writes only through passthrough rules."""

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AUTH_SERVICE_ENV", "/nonexistent-env-for-tests")

import passthrough  # noqa: E402
from datetime import datetime  # noqa: E402
from unittest import mock  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

_ET = ZoneInfo("America/New_York")
# Pin the clock mid-week so opening-order tests don't depend on the day they run.
_WEDNESDAY = datetime(2026, 11, 11, 11, 0, tzinfo=_ET)
passthrough._now = lambda: _WEDNESDAY


def _option_order(**overrides):
    body = {
        "account": "https://api.robinhood.com/accounts/TESTACCT/",
        "direction": "debit", "type": "limit", "time_in_force": "gfd",
        "quantity": "1", "price": "6.00", "ref_id": str(uuid.uuid4()),
        "legs": [
            {"option": "https://api.robinhood.com/options/instruments/a/",
             "side": "buy", "position_effect": "open", "ratio_quantity": 1},
            {"option": "https://api.robinhood.com/options/instruments/b/",
             "side": "sell", "position_effect": "open", "ratio_quantity": 1},
        ],
    }
    body.update(overrides)
    return body


def _equity_order(**overrides):
    body = {"account": "https://api.robinhood.com/accounts/TESTACCT/",
            "instrument": "https://api.robinhood.com/instruments/x/", "symbol": "NBIS",
            "side": "buy", "type": "limit", "quantity": "2", "price": "158.25",
            "time_in_force": "gfd", "trigger": "immediate", "ref_id": str(uuid.uuid4())}
    body.update(overrides)
    return body


class UpstreamPathTests(unittest.TestCase):
    def test_maps_prefix_and_keeps_query(self):
        self.assertEqual(passthrough.upstream_path("/rh/options/orders/?state=open"),
                         "/options/orders/?state=open")

    def test_rejects_host_smuggling_and_traversal(self):
        for bad in ("/rh//evil.com/x", "/rh/../token", "/rh/https://evil.com/",
                    "/rhx/orders/", "/rh/orders/%2e%2e/"):
            self.assertIsNone(passthrough.upstream_path(bad), bad)


class WriteRuleTests(unittest.TestCase):
    def test_vertical_spread_allowed(self):
        self.assertIsNone(passthrough.check_write("POST", "/options/orders/", _option_order()))

    def test_naked_short_open_blocked(self):
        legs = [{"option": "x", "side": "sell", "position_effect": "open"}]
        self.assertIn("naked", passthrough.check_write(
            "POST", "/options/orders/", _option_order(legs=legs)))

    def test_closing_sell_allowed(self):
        legs = [{"option": "x", "side": "sell", "position_effect": "close"}]
        self.assertIsNone(passthrough.check_write(
            "POST", "/options/orders/", _option_order(legs=legs, price="1.30")))

    def test_option_notional_cap(self):
        # 29 x $1.30 x 100 = $3,770 > default $1,000
        self.assertIn("options_max_notional", passthrough.check_write(
            "POST", "/options/orders/", _option_order(quantity="29", price="1.30")))

    def test_shape_left_to_robinhood(self):
        # Fractional contracts, odd leg fields: Robinhood rejects these, not us.
        self.assertIsNone(passthrough.check_write(
            "POST", "/options/orders/", _option_order(quantity="1.5")))
        self.assertIsNone(passthrough.check_write(
            "POST", "/options/orders/", _option_order(legs="not-a-list")))

    def test_unparseable_cap_fields_fail_closed(self):
        for bad in ({"quantity": ["29"]}, {"price": "abc"}, {"quantity": None}):
            self.assertIn("needed for", passthrough.check_write(
                "POST", "/options/orders/", _option_order(**bad)), bad)

    def test_naked_short_check_ignores_case(self):
        legs = [{"option": "x", "side": "SELL", "position_effect": "Open"}]
        self.assertIn("naked", passthrough.check_write(
            "POST", "/options/orders/", _option_order(legs=legs)))

    def test_equity_cap_and_ref_id(self):
        self.assertIsNone(passthrough.check_write("POST", "/orders/", _equity_order()))
        self.assertIn("equity_max_notional", passthrough.check_write(
            "POST", "/orders/", _equity_order(quantity="100")))
        self.assertIn("ref_id", passthrough.check_write(
            "POST", "/orders/", _equity_order(ref_id="nope")))

    def test_cancels_allowed(self):
        oid = str(uuid.uuid4())
        self.assertIsNone(passthrough.check_write("POST", f"/orders/{oid}/cancel/", {}))
        self.assertIsNone(passthrough.check_write("POST", f"/options/orders/{oid}/cancel/", {}))

    def test_unlisted_writes_refused(self):
        for path in ("/ach/transfers/", "/accounts/TESTACCT/", "/options/orders/x/replace/"):
            self.assertIn("not an allowed write", passthrough.check_write("POST", path, {}))


if __name__ == "__main__":
    unittest.main()


class WeekendOpenTests(unittest.TestCase):
    def _check(self, when, legs, cutoff=15.0):
        with mock.patch.object(passthrough, "_now", lambda: when), \
                mock.patch.object(passthrough.config, "RH_OPTIONS_WEEKEND_CUTOFF_HOUR_ET", cutoff):
            return passthrough.check_write("POST", "/options/orders/",
                                           _option_order(legs=legs, price="1.00"))

    BUY_OPEN = [{"option": "x", "side": "buy", "position_effect": "open"}]
    SELL_CLOSE = [{"option": "x", "side": "sell", "position_effect": "close"}]

    def test_opens_blocked_from_friday_cutoff_through_sunday(self):
        for when in (datetime(2026, 11, 13, 15, 0, tzinfo=_ET),
                     datetime(2026, 11, 14, 10, 0, tzinfo=_ET),
                     datetime(2026, 11, 15, 23, 59, tzinfo=_ET)):
            self.assertIn("weekend", self._check(when, self.BUY_OPEN), when)

    def test_opens_allowed_before_cutoff_and_monday(self):
        for when in (datetime(2026, 11, 13, 14, 59, tzinfo=_ET),
                     datetime(2026, 11, 16, 0, 0, tzinfo=_ET)):
            self.assertIsNone(self._check(when, self.BUY_OPEN), when)

    def test_closes_always_allowed(self):
        self.assertIsNone(self._check(datetime(2026, 11, 14, 10, 0, tzinfo=_ET), self.SELL_CLOSE))

    def test_spread_with_any_open_leg_blocked(self):
        legs = self.BUY_OPEN + [{"option": "y", "side": "sell", "position_effect": "open"}]
        self.assertIn("weekend", self._check(datetime(2026, 11, 13, 16, 0, tzinfo=_ET), legs))

    def test_negative_cutoff_turns_rule_off(self):
        self.assertIsNone(self._check(datetime(2026, 11, 14, 10, 0, tzinfo=_ET),
                                      self.BUY_OPEN, cutoff=-1))
