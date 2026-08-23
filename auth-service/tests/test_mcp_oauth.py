import json
import time
import unittest
from unittest import mock

import mcp_oauth


def _blob(**overrides):
    base = {
        "access_token": "old-jwt",
        "refresh_token": "rt-1",
        "client_id": "client-abc",
        "token_endpoint": "https://api.robinhood.com/oauth2/token/",
        "resource": "https://agent.robinhood.com/mcp/trading",
        "scope": "internal",
        "expires_at": time.time() + 9 * 86400,
    }
    base.update(overrides)
    return base


class McpOAuthTests(unittest.TestCase):

    def test_needs_refresh_within_lead_window(self):
        # 1h left, 48h lead -> refresh now (well inside the observed ~4-9d TTL)
        obj = _blob(expires_at=time.time() + 3600)
        with mock.patch("mcp_oauth.config.MCP_REFRESH_LEAD_SECONDS", 172800):
            self.assertTrue(mcp_oauth.needs_refresh(obj))


    def test_refresh_blob_posts_oauth_fields(self):
        obj = _blob()
        resp = mock.Mock()
        resp.raise_for_status = mock.Mock()
        resp.json.return_value = {
            "access_token": "new-jwt",
            "refresh_token": "rt-2",
            "expires_in": 7200,
        }
        with mock.patch("mcp_oauth.requests.post", return_value=resp) as post:
            updated = mcp_oauth.refresh_blob(obj)
        post.assert_called_once()
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["grant_type"], "refresh_token")
        self.assertEqual(payload["refresh_token"], "rt-1")
        self.assertEqual(payload["client_id"], "client-abc")
        self.assertEqual(payload["resource"], obj["resource"])
        self.assertEqual(updated["access_token"], "new-jwt")
        self.assertEqual(updated["refresh_token"], "rt-2")
        self.assertGreater(updated["expires_at"], time.time())

    def test_from_secret_refreshes_and_persists_when_stale(self):
        stale = _blob(expires_at=time.time() - 10)
        refreshed = _blob(access_token="fresh-jwt", expires_at=time.time() + 7200)
        with mock.patch("mcp_oauth.get_secret", return_value=json.dumps(stale)), \
             mock.patch("mcp_oauth.refresh_blob", return_value=refreshed) as refresh, \
             mock.patch("mcp_oauth.add_secret_version") as persist, \
             mock.patch("mcp_oauth.config.MCP_TOKEN_SECRET", "rh-mcp-oauth-token"), \
             mock.patch("mcp_oauth.config.MCP_TOKEN", ""):
            token = mcp_oauth.get_access_token()
        self.assertEqual(token, "fresh-jwt")
        refresh.assert_called_once()
        persist.assert_called_once()


    def test_refresh_once_refreshes_when_due_and_swallows_errors(self):
        stale = _blob(expires_at=time.time() + 60)
        with mock.patch("mcp_oauth.config.MCP_TOKEN_SECRET", "rh-mcp-oauth-token"), \
             mock.patch("mcp_oauth.config.MCP_REFRESH_LEAD_SECONDS", 172800), \
             mock.patch("mcp_oauth.get_secret", return_value=json.dumps(stale)), \
             mock.patch("mcp_oauth.refresh_blob", side_effect=RuntimeError("invalid_grant")) as refresh:
            mcp_oauth.refresh_once()  # must not raise: loop stays alive
        refresh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
