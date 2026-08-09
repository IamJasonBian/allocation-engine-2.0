"""The /dashboard page: served, self-contained, and pointed at the Trading DB."""

import pytest
from flask import Flask

from app.api import register_blueprints


@pytest.fixture
def client(monkeypatch):
    # Engine thread never starts in tests; a bare Flask app with the
    # blueprints is the full surface under test here.
    app = Flask(__name__)
    register_blueprints(app)
    return app.test_client()


def test_dashboard_serves_html(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    body = r.get_data(as_text=True)
    assert "Allocation dashboard" in body
    # Reads the same functions the live site does.
    assert "5thstreetcapital.org/.netlify/functions" in body
    assert "order-book-snapshot" in body
    assert "db-bot-activity" in body


def test_dashboard_not_under_api_prefix(client):
    assert client.get("/api/dashboard").status_code == 404
