"""Test tiers as pytest markers.

    pytest                      # unit + smoke (integration/live auto-skipped)
    pytest -m smoke             # just the boot checks
    pytest --integration        # also run tests touching real local resources
    pytest --live               # also run tests against real services
    LIVE_TESTS=1 pytest -m live # env form, for CI / Render shell

Opt-in is deliberate: `live` needs credentials and network reach (the
auth-service box only admits Render egress IPs), and `integration` writes
real files. Neither should be a surprise on a laptop `pytest`.
"""

import os

import pytest
from flask import Flask

from app.api import register_blueprints
from app.config import Config

_TIERS = {
    "integration": ("--integration", "INTEGRATION_TESTS"),
    "live": ("--live", "LIVE_TESTS"),
}


def pytest_addoption(parser):
    parser.addoption("--integration", action="store_true", default=False,
                     help="run tests marked integration (real local resources)")
    parser.addoption("--live", action="store_true", default=False,
                     help="run tests marked live (real external services)")


def _enabled(config, flag, env):
    return config.getoption(flag) or os.getenv(env, "").strip() in ("1", "true", "yes")


def pytest_collection_modifyitems(config, items):
    for marker, (flag, env) in _TIERS.items():
        if _enabled(config, flag, env):
            continue
        skip = pytest.mark.skip(reason=f"{marker} tests need {flag} or {env}=1")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def api_client():
    """Flask test client over the real blueprints, engine thread never started."""
    app = Flask("allocation-engine-test")
    app.config.from_object(Config)
    register_blueprints(app)
    return app.test_client()


@pytest.fixture
def live_api_url():
    url = os.getenv("LIVE_API_URL", "").rstrip("/")
    if not url:
        pytest.skip("LIVE_API_URL not set (e.g. https://allocation-engine-api.onrender.com)")
    return url


@pytest.fixture
def live_box():
    """AuthServiceClient for the real box; skips when not configured.

    Reachability note: the box firewall admits Render egress IPs only. From a
    laptop, port-forward with `gcloud compute ssh ... -- -L 8443:localhost:8080`
    and point AUTH_SERVICE_URL at it.
    """
    from app.auth_service_client import AuthServiceClient
    if not (Config.AUTH_SERVICE_URL and Config.RH_AUTH_SERVICE_REQUEST_TOKEN):
        pytest.skip("AUTH_SERVICE_URL / RH_AUTH_SERVICE_REQUEST_TOKEN not set")
    return AuthServiceClient(timeout=15)
