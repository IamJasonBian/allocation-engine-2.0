"""Daily close fetch for marks (Yahoo chart endpoint)."""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict


def fetch_daily_closes(symbol: str, range_: str = "6mo") -> Dict[str, float]:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval=1d&range={range_}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                res = json.load(resp)["chart"]["result"][0]
            break
        except (http.client.IncompleteRead, urllib.error.URLError):
            if attempt == 4:
                raise
            time.sleep(1 + attempt)
    out: Dict[str, float] = {}
    for ts, c in zip(res.get("timestamp", []), res["indicators"]["quote"][0]["close"]):
        if c is not None:
            out[datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()] = float(c)
    return out
