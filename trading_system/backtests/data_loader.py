"""
Data loader for backtesting — fetches daily OHLCV via TwelveDataProvider
and caches results to avoid repeated API calls.
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from trading_system.data_providers.twelve_data import TwelveDataProvider

DATA_DIR = Path(__file__).parent / "data"


def load_daily_data(
    symbols: List[str],
    api_key: str,
    outputsize: int = 756,
    force_refresh: bool = False,
) -> Dict[str, List[Dict]]:
    """
    Fetch daily OHLCV bars for each symbol, caching to disk.

    Returns dict mapping symbol -> list of bars in chronological order
    (oldest first). Each bar: {date, open, high, low, close, volume}.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    provider = TwelveDataProvider(api_key)

    result = {}
    for i, symbol in enumerate(symbols):
        cache_path = DATA_DIR / f"{symbol}_daily.json"

        if not force_refresh and cache_path.exists():
            with open(cache_path, "r") as f:
                bars = json.load(f)
            print(f"  {symbol}: loaded {len(bars)} cached bars")
            result[symbol] = bars
            continue

        # Rate-limit: Twelve Data allows 8 calls/min on free tier
        if i > 0:
            print(f"  Rate-limiting: waiting 8s before next API call...")
            time.sleep(8)

        print(f"  {symbol}: fetching {outputsize} daily bars from Twelve Data...")
        raw = provider.get_daily_data(symbol, outputsize=outputsize)
        if raw is None:
            print(f"  WARNING: No data returned for {symbol}, skipping")
            continue

        # API returns newest-first; reverse to chronological order
        bars = list(reversed(raw))

        with open(cache_path, "w") as f:
            json.dump(bars, f, indent=2)
        print(f"  {symbol}: cached {len(bars)} bars to {cache_path.name}")

        result[symbol] = bars

    return result
