"""A cached token must be checked against Robinhood, not just the clock.

On 2026-08-04 Robinhood revoked the session ~18h before its stated expiry.
`check_token_valid` defaulted to a local `expires_at` comparison, so
`get_session` kept vending the dead token and `/auth/status` kept reporting
`authenticated: true` while every consumer 401'd for a day.
"""

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import session as sm  # noqa: E402
from models import AuthResult, Session  # noqa: E402


def _session(token="tok-live"):
    return Session(
        access_token=token,
        refresh_token="refresh",
        token_type="Bearer",
        expires_at=time.time() + 86_400,   # comfortably unexpired
        device_token="dev",
        account_number="5RX91619",
    )


class VerifyCachedTokenTests(unittest.TestCase):
    def setUp(self):
        sm._verified.clear()
        self.addCleanup(sm._verified.clear)

    def test_revoked_but_unexpired_token_is_not_served(self):
        live = _session()
        fresh = _session("tok-fresh")
        with mock.patch.object(sm, "load", return_value=live), \
             mock.patch.object(sm.robinhood, "check_token_valid",
                               side_effect=lambda s, **kw: not kw.get("verify")), \
             mock.patch.object(sm, "_do_auth",
                               return_value=AuthResult("OK", session=fresh)):
            result = sm.get_session("rh.auth")

        # Fell through to a real re-auth instead of returning the dead token
        self.assertEqual(result.status, "OK")
        self.assertEqual(result.session.access_token, "tok-fresh")

    def test_good_token_is_served_without_reauth(self):
        live = _session()
        with mock.patch.object(sm, "load", return_value=live), \
             mock.patch.object(sm.robinhood, "check_token_valid", return_value=True), \
             mock.patch.object(sm, "_do_auth") as do_auth:
            result = sm.get_session("rh.auth")

        self.assertEqual(result.session.access_token, "tok-live")
        do_auth.assert_not_called()

    def test_verification_is_throttled_not_per_call(self):
        live = _session()
        with mock.patch.object(sm.robinhood, "check_token_valid",
                               return_value=True) as check:
            for _ in range(5):
                self.assertTrue(sm._verify_cached("rh.auth", live))

        verified = [c for c in check.call_args_list if c.kwargs.get("verify")]
        self.assertEqual(len(verified), 1, "should hit RH once, then cache")

    def test_a_failure_is_never_cached(self):
        live = _session()
        with mock.patch.object(sm.robinhood, "check_token_valid",
                               return_value=False) as check:
            self.assertFalse(sm._verify_cached("rh.auth", live))
            self.assertFalse(sm._verify_cached("rh.auth", live))

        # Both calls re-checked — a dead token must not be remembered as "known bad"
        # and skipped; the next caller should still drive re-auth.
        self.assertEqual(len(check.call_args_list), 2)

    def test_a_rotated_token_is_reverified(self):
        with mock.patch.object(sm.robinhood, "check_token_valid",
                               return_value=True) as check:
            sm._verify_cached("rh.auth", _session("tok-a"))
            sm._verify_cached("rh.auth", _session("tok-b"))

        self.assertEqual(len(check.call_args_list), 2)

    def test_forced_login_discards_the_verification_cache(self):
        live = _session()
        sm._verified["rh.auth"] = (live.access_token, time.monotonic(), True)
        with mock.patch.object(sm, "_do_auth",
                               return_value=AuthResult("OK", session=_session("new"))):
            sm.get_session("rh.auth", force=True)
        self.assertNotIn("rh.auth", sm._verified)


class StatusTests(unittest.TestCase):
    def setUp(self):
        sm._verified.clear()
        self.addCleanup(sm._verified.clear)

    def test_status_reports_revoked_token_as_unauthenticated(self):
        with mock.patch.object(sm, "load", return_value=_session()), \
             mock.patch.object(sm.robinhood, "check_token_valid",
                               side_effect=lambda s, **kw: not kw.get("verify")):
            out = sm.status("rh.auth")

        self.assertFalse(out["authenticated"])   # the bug: this used to be True
        self.assertTrue(out["token_unexpired"])
        self.assertFalse(out["token_verified"])

    def test_status_can_skip_verification(self):
        with mock.patch.object(sm, "load", return_value=_session()), \
             mock.patch.object(sm.robinhood, "check_token_valid",
                               return_value=True) as check:
            out = sm.status("rh.auth", verify=False)

        self.assertIsNone(out["token_verified"])
        self.assertFalse(any(c.kwargs.get("verify") for c in check.call_args_list))


if __name__ == "__main__":
    unittest.main()
