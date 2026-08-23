"""Live: real services, read-only, opt-in (pytest --live).

Nothing here mutates state or prints a secret; failures mean "the world
changed", not "the code is wrong".
"""

import requests
import pytest

pytestmark = pytest.mark.live


def test_render_api_health(live_api_url):
    r = requests.get(f"{live_api_url}/api/health", timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "allocation-engine-2.0"


def test_render_api_auth_status_shape(live_api_url):
    body = requests.get(f"{live_api_url}/api/auth/status", timeout=15).json()
    assert {"authenticated", "device_challenge_pending"} <= body.keys()


def test_box_health_needs_no_auth(live_box):
    r = requests.get(f"{live_box.base_url}/health", timeout=15)
    assert r.status_code == 200


def test_box_vends_token_shape(live_box):
    tok = live_box.get_token()
    assert tok["token"] and tok.get("token_type", "Bearer") == "Bearer"
    assert float(tok["expires_at"]) > 0
    # never echo the token — only its shape is under test
