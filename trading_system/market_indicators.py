"""
Market Indicators Module
Fetches BTC and major index data, computes IV z-score, correlations,
historical volatility (Yang-Zhang), ETF flows, and 200-week MA.
Writes results to dashboard/market_data.json for the live dashboard.

Ported from allocation-manager's marketIndicatorService.ts
"""

import json
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

TWELVE_DATA_API = "https://api.twelvedata.com"
DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
DATA_FILE = DASHBOARD_DIR / "market_data.json"

# All instruments: focus (BTC, QQQ) + reference indexes
INSTRUMENTS = ["BTC", "BTC/USD", "QQQ", "SPY", "GLD", "DIA", "IWM", "EFA"]
INSTRUMENT_NAMES = {
    "BTC": "BTC Trust",
    "BTC/USD": "Bitcoin",
    "QQQ": "Nasdaq 100",
    "SPY": "S&P 500",
    "GLD": "Gold",
    "DIA": "Dow Jones",
    "IWM": "Russell 2000",
    "EFA": "MSCI EAFE",
}

# Cache raw API responses to avoid hammering rate limits
_cache: Dict[str, Tuple[float, any]] = {}
CACHE_TTL = 300  # 5 minutes
_last_api_call = 0.0


def _get_api_key() -> str:
    return os.getenv("TWELVE_DATA_API_KEY", "f2c57fbb0a794024b0defff74af45686")


def _rate_limit():
    """Pause between API calls to respect Twelve Data 8/minute limit."""
    global _last_api_call
    elapsed = time.time() - _last_api_call
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_api_call = time.time()


def _fetch_ohlcv(symbol: str, outputsize: int, interval: str = "1day") -> List[Dict]:
    """Fetch OHLCV data from Twelve Data with caching and rate limiting."""
    cache_key = f"{symbol}:{interval}:{outputsize}"
    now = time.time()
    if cache_key in _cache and now - _cache[cache_key][0] < CACHE_TTL:
        return _cache[cache_key][1]

    _rate_limit()

    api_key = _get_api_key()
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": str(outputsize),
        "apikey": api_key,
    }
    try:
        resp = requests.get(f"{TWELVE_DATA_API}/time_series", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [indicators] Failed to fetch {symbol} ({interval}): {e}")
        return _cache.get(cache_key, (0, []))[1]

    if data.get("status") == "error" or "values" not in data:
        print(f"  [indicators] API error for {symbol}: {data.get('message', 'no values')}")
        return _cache.get(cache_key, (0, []))[1]

    rows = []
    for item in data["values"]:
        dt_str = item["datetime"]
        try:
            if len(dt_str) > 10:
                ts = int(datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
            else:
                ts = int(datetime.strptime(dt_str, "%Y-%m-%d").timestamp() * 1000)
        except ValueError:
            ts = 0
        rows.append({
            "date": dt_str[:10],
            "datetime": dt_str,
            "timestamp": ts,
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"]),
            "volume": float(item.get("volume", 0)),
        })
    rows.reverse()  # oldest first

    _cache[cache_key] = (now, rows)
    return rows


# ── Statistical helpers ──────────────────────────────────────────────────

def _avg(arr: List[float]) -> float:
    return sum(arr) / len(arr) if arr else 0.0


def _stddev(arr: List[float]) -> float:
    if len(arr) < 2:
        return 0.0
    mean = _avg(arr)
    variance = sum((x - mean) ** 2 for x in arr) / (len(arr) - 1)
    return math.sqrt(variance)


def _pearson(a: List[float], b: List[float]) -> Optional[float]:
    """Pearson correlation between two equal-length series."""
    n = len(a)
    if n < 3 or len(b) != n:
        return None
    ma, mb = _avg(a), _avg(b)
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a) / (n - 1))
    sb = math.sqrt(sum((x - mb) ** 2 for x in b) / (n - 1))
    if sa == 0 or sb == 0:
        return None
    return cov / (sa * sb)


# ── Yang-Zhang Volatility ────────────────────────────────────────────────

def _yang_zhang_vol(data: List[Dict], trading_days: int = 365) -> float:
    """Yang-Zhang volatility estimator (annualised)."""
    n = len(data)
    if n < 3:
        return 0.0

    overnight_ret = []
    close_ret = []
    rs_components = []

    for i in range(1, n):
        o = data[i]["open"]
        h = data[i]["high"]
        l = data[i]["low"]
        c = data[i]["close"]
        prev_c = data[i - 1]["close"]

        overnight_ret.append(math.log(o / prev_c))
        close_ret.append(math.log(c / prev_c))
        rs_components.append(
            math.log(h / c) * math.log(h / o) + math.log(l / c) * math.log(l / o)
        )

    m = len(overnight_ret)
    k = 0.34 / (1.34 + (m + 1) / (m - 1))
    o_mean = _avg(overnight_ret)
    c_mean = _avg(close_ret)

    o_var = sum((r - o_mean) ** 2 for r in overnight_ret) / (m - 1)
    c_var = sum((r - c_mean) ** 2 for r in close_ret) / (m - 1)
    rs_var = _avg(rs_components)

    yz_var = o_var + k * c_var + (1 - k) * rs_var
    return math.sqrt(max(0, yz_var) * trading_days)


# ── Indicator Calculations ───────────────────────────────────────────────

def calc_iv_zscore(daily_data: List[Dict], lookback: int = 365) -> Dict:
    """IV Z-Score using 30-day realised volatility as proxy."""
    closes = [d["close"] for d in daily_data]
    if len(closes) < 40:
        return {"source": None, "current": None, "mean": None, "std": None,
                "zscore": None, "series": []}

    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    window = 30
    rolling_vol = []
    rolling_ts = []

    for i in range(window - 1, len(log_returns)):
        sl = log_returns[i - window + 1: i + 1]
        vol = _stddev(sl) * math.sqrt(365)
        rolling_vol.append(vol)
        rolling_ts.append(daily_data[i + 1]["timestamp"])

    if len(rolling_vol) < 30:
        return {"source": None, "current": None, "mean": None, "std": None,
                "zscore": None, "series": []}

    tail = rolling_vol[-lookback:]
    tail_ts = rolling_ts[-lookback:]
    current = tail[-1]
    mean_val = _avg(tail)
    std_val = _stddev(tail)

    return {
        "source": "30d Realised Vol",
        "current": round(current * 100, 2),
        "mean": round(mean_val * 100, 2),
        "std": round(std_val * 100, 2),
        "zscore": round((current - mean_val) / std_val, 2) if std_val > 0 else 0,
        "series": [
            {"timestamp": tail_ts[i], "value": round(tail[i] * 100, 2)}
            for i in range(len(tail))
        ],
    }


def calc_etf_flows(etf_data: List[Dict], btc_daily: List[Dict]) -> Dict:
    """Estimate ETF inflows/outflows from BTC ETF volume and premium over BTC/USD."""
    empty = {"etfCount": 0, "totalDollarVolume": 0, "netFlowEstimate": 0,
             "recent7d": 0, "recent30d": 0, "dailyFlows": []}

    if not etf_data or not btc_daily:
        return empty

    btc_returns = {}
    for i in range(1, len(btc_daily)):
        btc_returns[btc_daily[i]["date"]] = (
            (btc_daily[i]["close"] - btc_daily[i - 1]["close"]) / btc_daily[i - 1]["close"]
        )

    flow_map = {}
    total_dv = 0.0

    for i in range(1, len(etf_data)):
        date = etf_data[i]["date"]
        etf_ret = (etf_data[i]["close"] - etf_data[i - 1]["close"]) / etf_data[i - 1]["close"]
        btc_ret = btc_returns.get(date)
        if btc_ret is None:
            continue

        dollar_vol = etf_data[i]["close"] * etf_data[i]["volume"]
        total_dv += dollar_vol
        premium = etf_ret - btc_ret
        flow_sign = 1 if premium > 0 else (-1 if premium < 0 else 0)

        if date in flow_map:
            flow_map[date]["flow"] += dollar_vol * flow_sign
        else:
            flow_map[date] = {"timestamp": etf_data[i]["timestamp"],
                              "flow": dollar_vol * flow_sign}

    sorted_flows = sorted(flow_map.values(), key=lambda d: d["timestamp"])
    cumulative = 0.0
    daily_flows = []
    for d in sorted_flows:
        cumulative += d["flow"]
        daily_flows.append({
            "timestamp": d["timestamp"],
            "flow": round(d["flow"]),
            "cumulative": round(cumulative),
        })

    return {
        "etfCount": 1,
        "totalDollarVolume": round(total_dv),
        "netFlowEstimate": round(sum(d["flow"] for d in daily_flows)),
        "recent7d": round(sum(d["flow"] for d in daily_flows[-7:])),
        "recent30d": round(sum(d["flow"] for d in daily_flows[-30:])),
        "dailyFlows": daily_flows,
    }


def calc_200wk_ma(weekly_data: List[Dict]) -> Dict:
    """200-week moving average for BTC/USD."""
    if not weekly_data:
        return {"currentPrice": 0, "ma200wk": None, "ratio": None,
                "pctAbove": None, "series": []}

    closes = [d["close"] for d in weekly_data]
    ma_values = []
    for i in range(len(closes)):
        if i < 199:
            ma_values.append(None)
        else:
            ma_values.append(_avg(closes[i - 199: i + 1]))

    current_price = closes[-1]
    current_ma = ma_values[-1]

    return {
        "currentPrice": round(current_price, 2),
        "ma200wk": round(current_ma, 2) if current_ma else None,
        "ratio": round(current_price / current_ma, 4) if current_ma else None,
        "pctAbove": round((current_price / current_ma - 1) * 100, 2) if current_ma else None,
        "series": [
            {"timestamp": weekly_data[i]["timestamp"],
             "price": round(weekly_data[i]["close"], 2),
             "ma": round(ma_values[i], 2) if ma_values[i] is not None else None}
            for i in range(len(weekly_data))
        ],
    }


def calc_historical_vol(daily_data: List[Dict]) -> Dict:
    """Historical volatility: multi-window + 30d rolling (Yang-Zhang)."""
    n = len(daily_data)

    windows = []
    for label, period in [("30d", 30), ("60d", 60), ("90d", 90), ("1Y", 365)]:
        if n < period + 2:
            continue
        vol = _yang_zhang_vol(daily_data[-period:])
        regime = "normal"
        if vol > 0.8:
            regime = "extreme"
        elif vol > 0.5:
            regime = "high"
        elif vol < 0.2:
            regime = "low"
        windows.append({"label": label, "vol": round(vol * 100, 2), "regime": regime})

    rolling_series = []
    for i in range(30, n):
        vol = _yang_zhang_vol(daily_data[i - 30: i])
        rolling_series.append({
            "timestamp": daily_data[i]["timestamp"],
            "vol": round(vol * 100, 2),
        })

    return {"windows": windows, "rollingSeries": rolling_series}


# ── Hourly-granularity indicators ────────────────────────────────────────

# Annualisation: crypto trades 24/7 (8760 hrs/yr), stocks ~6.5h * 252d (1638 hrs/yr)
CRYPTO_HOURS_PER_YEAR = 8760
EQUITY_HOURS_PER_YEAR = 1638


def calc_hourly_iv_zscore(hourly_data: List[Dict], is_crypto: bool = False) -> Dict:
    """
    IV Z-Score from hourly close-to-close returns.
    Uses a 168-hour (1 week) rolling window for the vol estimate,
    then z-scores across the full available history.
    """
    ann = CRYPTO_HOURS_PER_YEAR if is_crypto else EQUITY_HOURS_PER_YEAR
    closes = [d["close"] for d in hourly_data]
    if len(closes) < 200:
        return {"source": None, "current": None, "mean": None, "std": None,
                "zscore": None, "series": []}

    log_ret = [math.log(closes[i] / closes[i - 1])
               for i in range(1, len(closes)) if closes[i - 1] > 0]
    window = 168  # 1 week of hourly bars
    rolling_vol = []
    rolling_ts = []

    for i in range(window - 1, len(log_ret)):
        sl = log_ret[i - window + 1: i + 1]
        vol = _stddev(sl) * math.sqrt(ann)
        rolling_vol.append(vol)
        rolling_ts.append(hourly_data[i + 1]["timestamp"])

    if len(rolling_vol) < 30:
        return {"source": None, "current": None, "mean": None, "std": None,
                "zscore": None, "series": []}

    current = rolling_vol[-1]
    mean_val = _avg(rolling_vol)
    std_val = _stddev(rolling_vol)

    return {
        "source": "168h Realised Vol",
        "current": round(current * 100, 2),
        "mean": round(mean_val * 100, 2),
        "std": round(std_val * 100, 2),
        "zscore": round((current - mean_val) / std_val, 2) if std_val > 0 else 0,
        "series": [
            {"timestamp": rolling_ts[i], "value": round(rolling_vol[i] * 100, 2)}
            for i in range(len(rolling_vol))
        ],
    }


def calc_hourly_vol(hourly_data: List[Dict], is_crypto: bool = False) -> Dict:
    """
    Historical volatility from hourly OHLCV (Yang-Zhang).
    Rolling 24-hour window, annualised.
    """
    ann = CRYPTO_HOURS_PER_YEAR if is_crypto else EQUITY_HOURS_PER_YEAR
    n = len(hourly_data)

    # Spot windows
    windows = []
    for label, period in [("4h", 4), ("24h", 24), ("1w", 168)]:
        if n < period + 2:
            continue
        vol = _yang_zhang_vol(hourly_data[-period:], trading_days=ann)
        regime = "normal"
        if vol > 0.8:
            regime = "extreme"
        elif vol > 0.5:
            regime = "high"
        elif vol < 0.2:
            regime = "low"
        windows.append({"label": label, "vol": round(vol * 100, 2), "regime": regime})

    # Rolling 24h series
    rolling_series = []
    for i in range(24, n):
        vol = _yang_zhang_vol(hourly_data[i - 24: i], trading_days=ann)
        rolling_series.append({
            "timestamp": hourly_data[i]["timestamp"],
            "vol": round(vol * 100, 2),
        })

    return {"windows": windows, "rollingSeries": rolling_series}


# ── Instrument data + correlations ───────────────────────────────────────

def _build_quote(series: List[Dict]) -> Optional[Dict]:
    """Build a quote dict from the latest bars of a daily series."""
    if not series or len(series) < 2:
        return None
    last = series[-1]
    prev = series[-2]
    change = last["close"] - prev["close"]
    change_pct = (change / prev["close"]) * 100 if prev["close"] else 0
    return {
        "price": round(last["close"], 4),
        "open": round(last["open"], 4),
        "high": round(last["high"], 4),
        "low": round(last["low"], 4),
        "prev_close": round(prev["close"], 4),
        "change": round(change, 4),
        "change_pct": round(change_pct, 2),
        "volume": last["volume"],
        "date": last["date"],
    }


def _daily_returns(series: List[Dict]) -> Dict[str, float]:
    """Compute daily log returns keyed by date string."""
    ret = {}
    for i in range(1, len(series)):
        if series[i - 1]["close"] > 0 and series[i]["close"] > 0:
            ret[series[i]["date"]] = math.log(series[i]["close"] / series[i - 1]["close"])
    return ret


def _compute_correlations(
    daily_data: Dict[str, List[Dict]],
    focus: List[str],
    reference: List[str],
    windows: List[int],
) -> Dict:
    """Compute correlation of each focus instrument vs each reference for given day windows."""
    # Pre-compute returns for all instruments
    returns_by_sym = {}
    for sym, series in daily_data.items():
        returns_by_sym[sym] = _daily_returns(series)

    result = {}
    for w in windows:
        w_key = str(w)
        result[w_key] = {}
        for f in focus:
            if f not in returns_by_sym:
                continue
            f_ret = returns_by_sym[f]
            for r in reference:
                if r == f or r not in returns_by_sym:
                    continue
                r_ret = returns_by_sym[r]
                # Align on common dates, take last w
                common = sorted(set(f_ret.keys()) & set(r_ret.keys()))
                common = common[-w:] if len(common) >= w else common
                if len(common) < 5:
                    continue
                a = [f_ret[d] for d in common]
                b = [r_ret[d] for d in common]
                corr = _pearson(a, b)
                if corr is not None:
                    result[w_key][f"{f}_{r}"] = round(corr, 3)
    return result


def _compact_series(series: List[Dict]) -> List[Dict]:
    """Compact a series to just timestamp + close for JSON size."""
    return [{"t": d["timestamp"], "c": round(d["close"], 4)} for d in series]


def _compact_hourly(series: List[Dict]) -> List[Dict]:
    """Compact hourly series with OHLCV."""
    return [{
        "t": d["timestamp"],
        "o": round(d["open"], 4),
        "h": round(d["high"], 4),
        "l": round(d["low"], 4),
        "c": round(d["close"], 4),
    } for d in series]


# ── Main entry point ─────────────────────────────────────────────────────

def fetch_and_write_indicators(symbols: Optional[List[str]] = None,
                               extra_data: Optional[Dict] = None) -> Optional[str]:
    """
    Fetch market indicator data and write to dashboard/market_data.json.
    extra_data: optional dict merged into the output (e.g. options positions).
    Returns the output file path on success.
    """
    print("\n  [indicators] Fetching market indicator data...")

    # -- Fetch daily + hourly for all instruments --
    daily_data: Dict[str, List[Dict]] = {}
    hourly_data: Dict[str, List[Dict]] = {}
    quotes: Dict[str, Dict] = {}

    for sym in INSTRUMENTS:
        print(f"  [indicators]   {sym}...", end="", flush=True)
        d = _fetch_ohlcv(sym, 500, "1day")
        if d:
            daily_data[sym] = d
        h = _fetch_ohlcv(sym, 500, "1h")
        if h:
            hourly_data[sym] = h
        q = _build_quote(d) if d else None
        if q:
            quotes[sym] = q
        print(f" {len(d)} daily, {len(h)} hourly" if d and h else " (partial)")

    # -- BTC indicator calculations (use BTC/USD daily for crypto metrics) --
    btc_daily = daily_data.get("BTC/USD", [])
    btc_weekly = _fetch_ohlcv("BTC/USD", 270, "1week")
    btc_etf = daily_data.get("BTC", [])

    iv = calc_iv_zscore(btc_daily) if btc_daily else {
        "source": None, "current": None, "mean": None, "std": None,
        "zscore": None, "series": [],
    }
    flows = calc_etf_flows(btc_etf, btc_daily)
    ma = calc_200wk_ma(btc_weekly)
    vol = calc_historical_vol(btc_daily) if btc_daily else {"windows": [], "rollingSeries": []}

    # -- Hourly-granularity indicators (from BTC/USD hourly bars) --
    btc_hourly = hourly_data.get("BTC/USD", [])
    hourly_iv = calc_hourly_iv_zscore(btc_hourly, is_crypto=True) if btc_hourly else {
        "source": None, "current": None, "mean": None, "std": None,
        "zscore": None, "series": [],
    }
    hourly_vol = calc_hourly_vol(btc_hourly, is_crypto=True) if btc_hourly else {
        "windows": [], "rollingSeries": [],
    }

    # -- Correlations: BTC and QQQ vs major indexes --
    corr_windows = [5, 10, 15, 20, 25, 30, 35]  # 1W through 7W in trading days
    focus = ["BTC", "BTC/USD"]
    reference = ["QQQ", "SPY", "GLD", "DIA", "IWM", "EFA"]
    correlations = _compute_correlations(daily_data, focus, reference, corr_windows)

    # -- Build result JSON --
    result = {
        "updated_at": datetime.now().isoformat(),
        "instrument_names": INSTRUMENT_NAMES,
        "quotes": quotes,
        "hourly": {sym: _compact_hourly(s) for sym, s in hourly_data.items()},
        "daily": {sym: _compact_series(s) for sym, s in daily_data.items()},
        "correlations": correlations,
        "iv": iv,
        "flows": flows,
        "ma": ma,
        "vol": vol,
        "hourly_iv": hourly_iv,
        "hourly_vol": hourly_vol,
    }
    if extra_data:
        result.update(extra_data)

    DASHBOARD_DIR.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(result))
    print(f"  [indicators] Dashboard data written to {DATA_FILE}")
    return str(DATA_FILE)
