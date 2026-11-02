"""Guardrails and dry-run behavior of POST /api/trade/option-spread."""

from datetime import date

import pytest
from flask import Flask

from app.api import register_blueprints
from app.api.trade import validate_spread

TOKEN = "t0ken"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TODAY = date(2026, 11, 3)
GOOD = {"symbol": "AMD", "expiration": "2026-11-04", "option_type": "call",
        "long_strike": 470, "short_strike": 510, "quantity": 1, "limit_price": 12.8}


def _client(dry=True):
    app = Flask(__name__)
    register_blueprints(app)
    app.config.update(DRY_RUN=dry, DEFAULT_BROKER="robinhood", DASHBOARD_REQUEST_TOKEN=TOKEN)
    return app.test_client()


def test_valid_spread_normalizes():
    spread, errs = validate_spread(GOOD, today=TODAY)
    assert errs == [] and spread["limit_price"] == 12.8 and spread["symbol"] == "AMD"


@pytest.mark.parametrize("patch,needle", [
    ({"symbol": "NBIS"}, "symbol"),
    ({"quantity": 3}, "quantity"),
    ({"limit_price": 40}, "strike width"),
    ({"limit_price": 0}, "strike width"),
    ({"long_strike": 510, "short_strike": 470}, "long strike below"),
    ({"expiration": "2026-12-18"}, "expiration"),
    ({"quantity": 2, "limit_price": 20}, "cap"),
])
def test_guardrails_reject(patch, needle):
    _, errs = validate_spread({**GOOD, **patch}, today=TODAY)
    assert any(needle in e for e in errs), errs


def test_requires_token():
    assert _client().post("/api/trade/option-spread", json=GOOD).status_code == 401


def test_dry_run_by_default_even_when_global_dry_run_off(monkeypatch):
    monkeypatch.setattr("app.api.trade.date", type("D", (), {"today": staticmethod(lambda: TODAY)}))
    r = _client(dry=False).post("/api/trade/option-spread", json=GOOD, headers=AUTH)
    assert r.status_code == 200 and r.get_json()["status"] == "simulated"
    assert r.get_json()["max_loss_usd"] == 1280.0
