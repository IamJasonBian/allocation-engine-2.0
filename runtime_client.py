"""Client for the allocation-runtime-service read-only API."""

import requests
from config import RUNTIME_SERVICE_URL


class RuntimeClient:
    def __init__(self, base_url: str = RUNTIME_SERVICE_URL):
        self.base = base_url.rstrip("/")

    def _get(self, path: str):
        resp = requests.get(f"{self.base}{path}", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def health(self) -> dict:
        return self._get("/health")

    def state(self) -> dict:
        """Latest trading state (tickers, drift metrics, strategy state)."""
        return self._get("/state")

    def orders(self) -> dict:
        """Open stock orders + options positions from latest snapshot."""
        return self._get("/orders")

    def portfolio(self) -> dict:
        """Portfolio holdings from latest snapshot."""
        return self._get("/portfolio")

    def market_data(self) -> dict:
        """Ticker-level metrics and drift analysis."""
        return self._get("/market-data")

    def snapshots(self) -> dict:
        """List most recent 50 snapshot keys."""
        return self._get("/snapshots")

    def snapshot(self, key: str) -> dict:
        """Full data for a specific snapshot."""
        return self._get(f"/snapshots?key={key}")
