"""
Parameter optimization, rolling metrics, drift detection, and regime suggestions.

- grid_search_params: 3D grid search over DCA simulation parameters
- compute_rolling_metrics: rolling Sharpe, completion rate, vol
- detect_drift: flag dates where rolling metrics cross thresholds
- suggest_regime_params: recommend parameter adjustments based on volatility regime
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from trading_system.backtests.metrics import compute_metrics
from trading_system.backtests.simulation import DailySnapshot, run_simulation


# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------

@dataclass
class GridSearchResult:
    """Ranked parameter combinations with metrics."""
    rows: List[Dict]  # sorted by objective (best first)
    best: Dict        # top row
    grid_shape: Tuple[int, int, int]  # (stop_offset_count, buy_offset_count, coverage_count)


def grid_search_params(
    bars: List[Dict],
    symbol: str,
    shares: int,
    price: float,
    stop_offsets: Optional[List[float]] = None,
    buy_offsets: Optional[List[float]] = None,
    coverages: Optional[List[float]] = None,
) -> GridSearchResult:
    """
    3D grid search over stop_offset_pct x buy_offset x coverage_threshold.

    Objective: maximize Sharpe ratio.

    Default grid:
      stop_offset: [0.005, 0.01125, 0.0175, 0.02375, 0.03]  (5 values)
      buy_offset:  [0.10, 0.20, 0.30, 0.40, 0.50]            (5 values)
      coverage:    [0.10, 0.20, 0.30]                          (3 values)
      Total: 75 combos
    """
    if stop_offsets is None:
        stop_offsets = [0.005, 0.01125, 0.0175, 0.02375, 0.03]
    if buy_offsets is None:
        buy_offsets = [0.10, 0.20, 0.30, 0.40, 0.50]
    if coverages is None:
        coverages = [0.10, 0.20, 0.30]

    rows: List[Dict] = []
    total = len(stop_offsets) * len(buy_offsets) * len(coverages)
    done = 0

    for so in stop_offsets:
        for bo in buy_offsets:
            for cov in coverages:
                result = run_simulation(
                    bars=bars,
                    symbol=symbol,
                    initial_shares=shares,
                    initial_price=price,
                    stop_offset_pct=so,
                    buy_offset=bo,
                    coverage_threshold=cov,
                )
                m = compute_metrics(result)
                rows.append({
                    "stop_offset_pct": so,
                    "buy_offset": bo,
                    "coverage_threshold": cov,
                    "sharpe_ratio": m.sharpe_ratio,
                    "total_return_pct": m.total_return_pct,
                    "annualized_return_pct": m.annualized_return_pct,
                    "max_drawdown_pct": m.max_drawdown_pct,
                    "completion_rate_pct": m.completion_rate_pct,
                    "pairs_placed": m.pairs_placed,
                    "pairs_completed": m.pairs_completed,
                    "excess_return_pct": m.excess_return_pct,
                })
                done += 1
                if done % 25 == 0:
                    print(f"    Grid search: {done}/{total} combos evaluated...")

    # Sort by Sharpe (descending)
    rows.sort(key=lambda r: r["sharpe_ratio"], reverse=True)

    return GridSearchResult(
        rows=rows,
        best=rows[0] if rows else {},
        grid_shape=(len(stop_offsets), len(buy_offsets), len(coverages)),
    )


def walk_forward_cv(
    bars: List[Dict],
    symbol: str,
    shares: int,
    price: float,
    n_folds: int = 3,
    test_pct: float = 0.50,
) -> List[Dict]:
    """Walk-forward cross-validation with expanding training window.

    The last ``test_pct`` fraction of *bars* is reserved for test folds.
    Each fold trains on all bars before its test window (expanding window),
    runs grid search to find the best params, then evaluates on the held-out
    test chunk.

    Args:
        bars: Daily OHLCV bars (chronological order).
        symbol: Ticker symbol.
        shares: Initial share count for simulation.
        price: Initial price for simulation.
        n_folds: Number of test folds.
        test_pct: Fraction of bars reserved for test folds (default 0.50).

    Returns:
        List of per-fold result dicts.
    """
    test_total = int(len(bars) * test_pct)
    test_chunk = test_total // n_folds
    train_start = len(bars) - test_total  # where the first test fold begins

    fold_results: List[Dict] = []

    for i in range(n_folds):
        test_begin = train_start + i * test_chunk
        # Last fold gets any remainder bars
        test_end = (test_begin + test_chunk) if i < n_folds - 1 else len(bars)

        bars_train = bars[:test_begin]
        bars_test = bars[test_begin:test_end]

        if len(bars_train) < 10 or len(bars_test) < 5:
            continue

        print(f"    Fold {i + 1}/{n_folds}: train {len(bars_train)} bars, test {len(bars_test)} bars")
        grid_result = grid_search_params(bars_train, symbol, shares, price)
        best = grid_result.best

        # Evaluate best params on the test chunk
        test_initial_price = bars_train[-1]["close"]
        test_snapshots = run_simulation(
            bars=bars_test,
            symbol=symbol,
            initial_shares=shares,
            initial_price=test_initial_price,
            stop_offset_pct=best["stop_offset_pct"],
            buy_offset=best["buy_offset"],
            coverage_threshold=best["coverage_threshold"],
        )
        test_metrics = compute_metrics(test_snapshots)

        fold_results.append({
            "fold": i + 1,
            "train_bars": len(bars_train),
            "test_bars": len(bars_test),
            "train_sharpe": round(best["sharpe_ratio"], 4),
            "test_sharpe": round(test_metrics.sharpe_ratio, 4),
            "test_return_pct": round(test_metrics.total_return_pct, 4),
            "test_max_drawdown_pct": round(test_metrics.max_drawdown_pct, 4),
            "best_params": {
                "stop_offset_pct": best["stop_offset_pct"],
                "buy_offset": best["buy_offset"],
                "coverage_threshold": best["coverage_threshold"],
            },
            "top_10": [row for row in grid_result.rows[:10]],
        })

    return fold_results


def run_regime_grid_search(
    bars: List[Dict],
    symbol: str,
    shares: int,
    price: float,
) -> Dict:
    """Run walk-forward CV and aggregate out-of-sample results.

    Uses walk-forward cross-validation (3 folds, expanding training window)
    instead of a single 80/20 holdout.  The last fold's best params are
    returned as the recommended parameters since that fold trains on the
    most data and is most recent.

    Args:
        bars: Daily OHLCV bars (chronological order).
        symbol: Ticker symbol.
        shares: Initial share count for simulation.
        price: Initial price for simulation.

    Returns:
        Dict with timestamp, symbol, best params/metrics, per-fold CV
        detail, aggregated Sharpe stats, and backward-compat keys.
    """
    from datetime import datetime, timezone

    # --- Walk-forward CV ---
    fold_results = walk_forward_cv(bars, symbol, shares, price)

    if not fold_results:
        # Fallback: not enough data for CV — return minimal result
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "regime": "UNKNOWN",
            "rolling_vol_pct": 0.0,
            "best": {},
            "all_rows": [],
            "cv_folds": [],
            "n_folds": 0,
            "mean_train_sharpe": None,
            "mean_test_sharpe": None,
            "std_test_sharpe": None,
            "degradation": None,
            "train_bars": 0,
            "test_bars": 0,
            "train_sharpe": 0,
            "test_sharpe": 0,
        }

    # Aggregate across folds
    train_sharpes = [f["train_sharpe"] for f in fold_results]
    test_sharpes = [f["test_sharpe"] for f in fold_results]

    mean_train_sharpe = sum(train_sharpes) / len(train_sharpes)
    mean_test_sharpe = sum(test_sharpes) / len(test_sharpes)

    if len(test_sharpes) >= 2:
        variance = sum((s - mean_test_sharpe) ** 2 for s in test_sharpes) / (len(test_sharpes) - 1)
        std_test_sharpe = math.sqrt(variance) if variance > 0 else 0.0
    else:
        std_test_sharpe = 0.0

    if mean_train_sharpe > 0:
        degradation = round(mean_test_sharpe / mean_train_sharpe, 4)
    else:
        degradation = None

    # Use the last fold's best params (trained on most data, most recent)
    last_fold = fold_results[-1]
    total_test_bars = sum(f["test_bars"] for f in fold_results)

    # Determine current regime from rolling 21d vol of last window
    if len(bars) >= 22:
        closes = [b["close"] for b in bars[-22:]]
        daily_rets = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                daily_rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
        if len(daily_rets) >= 2:
            mean_r = sum(daily_rets) / len(daily_rets)
            var = sum((r - mean_r) ** 2 for r in daily_rets) / (len(daily_rets) - 1)
            ann_vol = math.sqrt(var) * math.sqrt(252) * 100 if var > 0 else 0.0
        else:
            ann_vol = 0.0
    else:
        ann_vol = 0.0

    if ann_vol > 30.0:
        regime = "HIGH_VOL"
    elif ann_vol < 15.0:
        regime = "LOW_VOL"
    else:
        regime = "MEDIUM_VOL"

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "regime": regime,
        "rolling_vol_pct": round(ann_vol, 2),
        "best": last_fold["best_params"],
        "all_rows": last_fold["top_10"],
        "cv_folds": fold_results,
        "n_folds": len(fold_results),
        "mean_train_sharpe": round(mean_train_sharpe, 4),
        "mean_test_sharpe": round(mean_test_sharpe, 4),
        "std_test_sharpe": round(std_test_sharpe, 4),
        "degradation": degradation,
        # Backward-compat keys
        "train_bars": last_fold["train_bars"],
        "test_bars": total_test_bars,
        "train_sharpe": round(last_fold["train_sharpe"], 4),
        "test_sharpe": round(mean_test_sharpe, 4),
    }


# ---------------------------------------------------------------------------
# Grid cache I/O
# ---------------------------------------------------------------------------

_CACHE_DIR = Path(__file__).parent / "data"


def save_grid_cache(result: dict, symbol: str) -> Path:
    """Write grid search result to backtests/data/{symbol}_grid_cache.json."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{symbol}_grid_cache.json"
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    return path


def load_grid_cache(symbol: str) -> Optional[Dict]:
    """Read grid cache; return None if missing or older than 7 days."""
    path = _CACHE_DIR / f"{symbol}_grid_cache.json"
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        # Check staleness (7 days)
        from datetime import datetime, timezone, timedelta
        ts = data.get("timestamp")
        if ts:
            cache_time = datetime.fromisoformat(ts)
            if datetime.now(timezone.utc) - cache_time > timedelta(days=7):
                return None
        return data
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Rolling metrics
# ---------------------------------------------------------------------------

def compute_rolling_metrics(
    snapshots: List[DailySnapshot],
    window: int = 21,
    risk_free_rate: float = 0.05,
) -> List[Dict]:
    """
    Compute rolling metrics over a sliding window of daily snapshots.

    Returns a list of dicts: {date, rolling_sharpe, rolling_completion_rate,
    rolling_return, rolling_vol}
    """
    if len(snapshots) < window + 1:
        return []

    results: List[Dict] = []
    daily_rf = risk_free_rate / 252

    for i in range(window, len(snapshots)):
        win_snaps = snapshots[i - window: i + 1]

        # Daily returns in window
        daily_returns = []
        for j in range(1, len(win_snaps)):
            prev = win_snaps[j - 1].net_value
            if prev > 0:
                daily_returns.append((win_snaps[j].net_value - prev) / prev)

        # Rolling return
        start_val = win_snaps[0].net_value
        end_val = win_snaps[-1].net_value
        rolling_return = ((end_val - start_val) / start_val * 100) if start_val > 0 else 0.0

        # Rolling vol (annualized)
        if len(daily_returns) >= 2:
            mean_r = sum(daily_returns) / len(daily_returns)
            var = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
            std = math.sqrt(var) if var > 0 else 0.0
            rolling_vol = std * math.sqrt(252) * 100  # as percentage
        else:
            rolling_vol = 0.0

        # Rolling Sharpe
        if len(daily_returns) >= 2:
            mean_r = sum(daily_returns) / len(daily_returns)
            var = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
            std = math.sqrt(var) if var > 0 else 0.0
            if std > 0:
                rolling_sharpe = (mean_r - daily_rf) / std * math.sqrt(252)
            else:
                rolling_sharpe = 0.0
        else:
            rolling_sharpe = 0.0

        # Rolling completion rate: use coverage_pct as a proxy
        # (actual pair completion requires pair-level tracking, so we use
        # the coverage percentage which reflects active pair management)
        rolling_completion = win_snaps[-1].coverage_pct

        results.append({
            "date": snapshots[i].date,
            "rolling_sharpe": round(rolling_sharpe, 3),
            "rolling_completion_rate": round(rolling_completion, 1),
            "rolling_return": round(rolling_return, 2),
            "rolling_vol": round(rolling_vol, 2),
        })

    return results


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

def detect_drift(
    rolling_metrics: List[Dict],
    sharpe_threshold: float = -1.0,
    completion_threshold: float = 50.0,
) -> List[Dict]:
    """
    Flag dates where rolling Sharpe < sharpe_threshold or
    rolling completion rate < completion_threshold.

    Returns list of drift alerts: {date, metric, value, threshold}.
    """
    alerts: List[Dict] = []
    for rm in rolling_metrics:
        if rm["rolling_sharpe"] < sharpe_threshold:
            alerts.append({
                "date": rm["date"],
                "metric": "rolling_sharpe",
                "value": rm["rolling_sharpe"],
                "threshold": sharpe_threshold,
            })
        if rm["rolling_completion_rate"] < completion_threshold:
            alerts.append({
                "date": rm["date"],
                "metric": "rolling_completion_rate",
                "value": rm["rolling_completion_rate"],
                "threshold": completion_threshold,
            })
    return alerts


# ---------------------------------------------------------------------------
# Regime-based parameter suggestions
# ---------------------------------------------------------------------------

def suggest_regime_params(
    rolling_vol: float,
    current_params: Optional[Dict] = None,
    grid_search_best: Optional[Dict] = None,
) -> Dict:
    """
    Suggest parameter adjustments based on annualized volatility regime.

    If grid_search_best is provided (from a recent grid search), use its
    optimized parameters instead of hardcoded heuristics.

    Regimes:
      High vol (>30%): widen stop_offset, increase buy_offset
      Low vol (<15%):  tighten stop_offset, decrease buy_offset
      Medium:          keep defaults

    Args:
        rolling_vol: annualized volatility as percentage (e.g. 25.0 = 25%)
        current_params: current parameters (for reference)
        grid_search_best: best result from grid search (optional)

    Returns:
        Dict of suggested parameters with regime label.
    """
    if current_params is None:
        current_params = {
            "stop_offset_pct": 0.01,
            "buy_offset": 0.20,
            "coverage_threshold": 0.20,
        }

    # If grid search results are available, use them
    if (grid_search_best
            and "stop_offset_pct" in grid_search_best
            and "buy_offset" in grid_search_best
            and "coverage_threshold" in grid_search_best):
        if rolling_vol > 30.0:
            regime = "HIGH_VOL"
        elif rolling_vol < 15.0:
            regime = "LOW_VOL"
        else:
            regime = "MEDIUM_VOL"
        return {
            "regime": regime,
            "rolling_vol_pct": round(rolling_vol, 1),
            "stop_offset_pct": grid_search_best["stop_offset_pct"],
            "buy_offset": grid_search_best["buy_offset"],
            "coverage_threshold": grid_search_best["coverage_threshold"],
            "rationale": "Optimized via grid search (Sharpe-maximizing parameters)",
            "source": "grid_search",
            "grid_sharpe": grid_search_best.get("sharpe_ratio"),
        }

    if rolling_vol > 30.0:
        return {
            "regime": "HIGH_VOL",
            "rolling_vol_pct": round(rolling_vol, 1),
            "stop_offset_pct": 0.02,
            "buy_offset": 0.35,
            "coverage_threshold": 0.15,
            "rationale": "Widen stops and buy offsets to avoid whipsaw in high volatility",
            "source": "heuristic",
        }
    elif rolling_vol < 15.0:
        return {
            "regime": "LOW_VOL",
            "rolling_vol_pct": round(rolling_vol, 1),
            "stop_offset_pct": 0.0075,
            "buy_offset": 0.15,
            "coverage_threshold": 0.25,
            "rationale": "Tighten stops and offsets to capture smaller moves in low volatility",
            "source": "heuristic",
        }
    else:
        return {
            "regime": "MEDIUM_VOL",
            "rolling_vol_pct": round(rolling_vol, 1),
            "stop_offset_pct": current_params.get("stop_offset_pct", 0.01),
            "buy_offset": current_params.get("buy_offset", 0.20),
            "coverage_threshold": current_params.get("coverage_threshold", 0.20),
            "rationale": "Moderate volatility -- default parameters appropriate",
            "source": "heuristic",
        }
