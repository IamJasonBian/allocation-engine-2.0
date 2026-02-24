"""BTC protective put hedging strategy.

Fetches historical bars from Alpaca, estimates realized vol as IV proxy,
prices puts at various OTM levels, runs a backtest simulation on recent data,
and prints a recommendation.
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.enums import DataFeed
from alpaca.data.timeframe import TimeFrame

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY
from black_scholes import bs_put_price, bs_call_price, bs_delta_put, bs_gamma

log = logging.getLogger(__name__)

RISK_FREE_RATE = 0.043  # ~4.3% (current 1Y Treasury yield)
TRADING_DAYS = 252       # BTC ETF trades on equity calendar


# ── data loading ─────────────────────────────────────────────────────

def fetch_btc_bars(lookback_days: int = 90) -> list[dict]:
    """Fetch daily BTC (Grayscale Mini Trust) bars from Alpaca."""
    client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days + 5)  # buffer for weekends

    req = StockBarsRequest(
        symbol_or_symbols="BTC",
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    barset = client.get_stock_bars(req)

    bars = []
    for bar in barset["BTC"]:
        bars.append({
            "date": bar.timestamp.strftime("%Y-%m-%d"),
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
        })
    return bars[-lookback_days:]


# ── volatility estimation ────────────────────────────────────────────

def yang_zhang_vol(bars: list[dict], window: int = 21) -> float:
    """Yang-Zhang realized vol estimator (annualized)."""
    if len(bars) < window + 1:
        return _close_to_close_vol(bars, window)

    recent = bars[-window:]
    n = len(recent)

    log_oc = [math.log(b["close"] / b["open"]) for b in recent]
    log_co = [math.log(recent[i]["open"] / recent[i - 1]["close"])
              for i in range(1, n)]
    log_hl = [math.log(b["high"] / b["low"]) for b in recent]

    mean_oc = sum(log_oc) / n
    var_close = sum((x - mean_oc) ** 2 for x in log_oc) / (n - 1)

    if len(log_co) < 2:
        return _close_to_close_vol(bars, window)

    mean_co = sum(log_co) / len(log_co)
    var_open = sum((x - mean_co) ** 2 for x in log_co) / (len(log_co) - 1)

    # Rogers-Satchell component
    rs_vals = [
        math.log(b["high"] / b["close"]) * math.log(b["high"] / b["open"])
        + math.log(b["low"] / b["close"]) * math.log(b["low"] / b["open"])
        for b in recent
    ]
    var_rs = sum(rs_vals) / n

    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    yz_var = var_open + k * var_close + (1 - k) * var_rs

    return math.sqrt(max(yz_var, 0) * TRADING_DAYS)


def _close_to_close_vol(bars: list[dict], window: int = 21) -> float:
    recent = bars[-window:]
    log_returns = [math.log(recent[i]["close"] / recent[i - 1]["close"])
                   for i in range(1, len(recent))]
    if len(log_returns) < 2:
        return 0.5  # fallback
    mean = sum(log_returns) / len(log_returns)
    var = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    return math.sqrt(var * TRADING_DAYS)


# ── simulation ───────────────────────────────────────────────────────

@dataclass
class PutRoll:
    roll_date: str
    expiry_bar_idx: int
    strike: float
    premium_paid: float
    intrinsic_at_expiry: float = 0.0
    shares: int = 100


@dataclass
class HedgeSnapshot:
    date: str
    price: float
    equity_value: float
    put_mark: float
    cum_premium_paid: float
    cum_intrinsic: float
    net_value: float  # equity + put_mark - cum_premium + cum_intrinsic


@dataclass
class HedgeResult:
    bars_used: int
    iv: float
    otm_pct: float
    roll_days: int
    shares: int
    snapshots: list[HedgeSnapshot] = field(default_factory=list)
    rolls: list[PutRoll] = field(default_factory=list)


def run_put_hedge_backtest(
    bars: list[dict],
    shares: int = 100,
    iv: float | None = None,
    otm_pct: float = 0.05,
    roll_days: int = 21,
) -> HedgeResult:
    """Backtest protective put on historical bars.

    If iv is None, estimates from Yang-Zhang realized vol.
    """
    if iv is None:
        iv = yang_zhang_vol(bars)
        log.info("Estimated IV (Yang-Zhang 21d): %.1f%%", iv * 100)

    cum_premium = 0.0
    cum_intrinsic = 0.0
    active_roll: PutRoll | None = None
    next_roll_idx = 0
    snapshots: list[HedgeSnapshot] = []
    rolls: list[PutRoll] = []

    for idx, bar in enumerate(bars):
        price = bar["close"]

        # Settle expiring puts
        if active_roll and idx >= active_roll.expiry_bar_idx:
            intrinsic = max(active_roll.strike - price, 0.0)
            active_roll.intrinsic_at_expiry = intrinsic
            cum_intrinsic += intrinsic * active_roll.shares
            active_roll = None
            next_roll_idx = idx

        # Open new roll
        if idx >= next_roll_idx and active_roll is None:
            T = roll_days / TRADING_DAYS
            strike = round(price * (1.0 - otm_pct), 2)
            prem = bs_put_price(price, strike, T, RISK_FREE_RATE, iv)
            cum_premium += prem * shares

            roll = PutRoll(
                roll_date=bar["date"],
                expiry_bar_idx=idx + roll_days,
                strike=strike,
                premium_paid=prem,
                shares=shares,
            )
            rolls.append(roll)
            active_roll = roll
            next_roll_idx = idx + roll_days

        # Mark to market
        put_mark = 0.0
        if active_roll:
            remaining = max(active_roll.expiry_bar_idx - idx, 1)
            T_rem = remaining / TRADING_DAYS
            put_mark = bs_put_price(price, active_roll.strike, T_rem,
                                    RISK_FREE_RATE, iv) * active_roll.shares

        equity = shares * price
        net = equity + put_mark - cum_premium + cum_intrinsic

        snapshots.append(HedgeSnapshot(
            date=bar["date"], price=price, equity_value=equity,
            put_mark=put_mark, cum_premium_paid=cum_premium,
            cum_intrinsic=cum_intrinsic, net_value=net,
        ))

    return HedgeResult(
        bars_used=len(bars), iv=iv, otm_pct=otm_pct,
        roll_days=roll_days, shares=shares,
        snapshots=snapshots, rolls=rolls,
    )


# ── strike grid pricing ─────────────────────────────────────────────

@dataclass
class StrikeQuote:
    otm_pct: float
    strike: float
    premium_per_share: float
    total_premium: float
    delta: float
    gamma: float
    annual_cost_pct: float  # annualized premium as % of spot
    breakeven: float        # price where put payoff = premium


def price_put_grid(
    spot: float,
    iv: float,
    shares: int = 100,
    expiry_days: int = 21,
    otm_levels: list[float] | None = None,
) -> list[StrikeQuote]:
    """Price puts at various OTM levels for the current spot."""
    if otm_levels is None:
        otm_levels = [0.02, 0.05, 0.08, 0.10, 0.15, 0.20]

    T = expiry_days / TRADING_DAYS
    rolls_per_year = TRADING_DAYS / expiry_days
    grid = []

    for otm in otm_levels:
        strike = round(spot * (1.0 - otm), 2)
        prem = bs_put_price(spot, strike, T, RISK_FREE_RATE, iv)
        delta = bs_delta_put(spot, strike, T, RISK_FREE_RATE, iv)
        gamma = bs_gamma(spot, strike, T, RISK_FREE_RATE, iv)
        annual_cost = prem * rolls_per_year / spot * 100
        breakeven = strike - prem

        grid.append(StrikeQuote(
            otm_pct=otm, strike=strike,
            premium_per_share=round(prem, 4),
            total_premium=round(prem * shares, 2),
            delta=round(delta, 3),
            gamma=round(gamma, 4),
            annual_cost_pct=round(annual_cost, 2),
            breakeven=round(breakeven, 2),
        ))

    return grid


# ── reporting ────────────────────────────────────────────────────────

def print_hedge_report(
    bars: list[dict],
    positions: list[dict],
    shares_override: int | None = None,
):
    """Full hedge analysis: vol estimate, strike grid, backtest, recommendation."""
    spot = bars[-1]["close"]
    iv_21 = yang_zhang_vol(bars, window=21)
    iv_14 = yang_zhang_vol(bars, window=14)

    # Determine shares from Alpaca position or override
    btc_pos = [p for p in positions if p["symbol"] == "BTC"]
    if shares_override:
        shares = shares_override
    elif btc_pos:
        shares = int(abs(btc_pos[0]["qty"]))
    else:
        shares = 100  # default for analysis

    print(f"\n{'='*65}")
    print("BTC PROTECTIVE PUT HEDGE ANALYSIS")
    print(f"{'='*65}")
    print(f"  Spot:          ${spot:.2f}")
    print(f"  Position:      {shares} shares (${shares * spot:,.2f})")
    print(f"  IV (21d YZ):   {iv_21:.1%}")
    print(f"  IV (14d YZ):   {iv_14:.1%}")

    # Strike grid
    iv = iv_21
    grid = price_put_grid(spot, iv, shares=shares)

    print(f"\n  Strike Grid (21d expiry, IV={iv:.1%}):")
    print(f"  {'OTM%':>5} {'Strike':>8} {'Prem/sh':>8} {'Total':>9} "
          f"{'Delta':>6} {'Ann Cost%':>9} {'B/E':>8}")
    print(f"  {'-'*58}")
    for q in grid:
        print(f"  {q.otm_pct:>4.0%} {q.strike:>8.2f} {q.premium_per_share:>8.4f} "
              f"${q.total_premium:>8.2f} {q.delta:>6.3f} {q.annual_cost_pct:>8.2f}% "
              f"{q.breakeven:>8.2f}")

    # Backtest on last 60 bars
    bt_bars = bars[-60:] if len(bars) >= 60 else bars
    result = run_put_hedge_backtest(bt_bars, shares=shares, iv=iv, otm_pct=0.05)
    _print_backtest(result, spot)

    # Recommendation
    _print_recommendation(grid, iv, shares, spot)


def _print_backtest(result: HedgeResult, current_spot: float):
    if not result.snapshots:
        return

    first = result.snapshots[0]
    last = result.snapshots[-1]
    initial_eq = first.equity_value
    final_eq = last.equity_value

    # Unhedged return
    unhedged_return = (final_eq - initial_eq) / initial_eq * 100

    # Hedged return
    hedged_return = (last.net_value - initial_eq) / initial_eq * 100

    # Max drawdown unhedged
    peak = initial_eq
    max_dd_uh = 0.0
    for s in result.snapshots:
        if s.equity_value > peak:
            peak = s.equity_value
        dd = (peak - s.equity_value) / peak * 100
        max_dd_uh = max(max_dd_uh, dd)

    # Max drawdown hedged
    peak_h = initial_eq
    max_dd_h = 0.0
    for s in result.snapshots:
        if s.net_value > peak_h:
            peak_h = s.net_value
        dd = (peak_h - s.net_value) / peak_h * 100
        max_dd_h = max(max_dd_h, dd)

    total_premium = last.cum_premium_paid
    total_intrinsic = last.cum_intrinsic

    print(f"\n  Backtest ({result.bars_used}d, 5% OTM, {result.roll_days}d rolls):")
    print(f"    Unhedged return:   {unhedged_return:+.2f}%")
    print(f"    Hedged return:     {hedged_return:+.2f}%")
    print(f"    Hedge cost:        {unhedged_return - hedged_return:.2f}pp")
    print(f"    Max DD unhedged:   {max_dd_uh:.2f}%")
    print(f"    Max DD hedged:     {max_dd_h:.2f}%")
    print(f"    DD reduction:      {max_dd_uh - max_dd_h:.2f}pp")
    print(f"    Premium paid:      ${total_premium:,.2f}")
    print(f"    Intrinsic recv'd:  ${total_intrinsic:,.2f}")
    print(f"    Rolls:             {len(result.rolls)}")

    if result.rolls:
        print(f"\n    Roll history:")
        for r in result.rolls:
            settled = "open" if r.intrinsic_at_expiry == 0 and r.expiry_bar_idx > result.bars_used else "expired"
            if r.intrinsic_at_expiry > 0:
                settled = f"ITM +${r.intrinsic_at_expiry:.2f}"
            print(f"      {r.roll_date}  K={r.strike:.2f}  prem={r.premium_paid:.4f}  {settled}")


def _print_recommendation(grid: list[StrikeQuote], iv: float, shares: int, spot: float):
    # Recommend the 5% OTM put as the standard hedge
    rec = next((q for q in grid if q.otm_pct == 0.05), grid[0])

    print(f"\n  Recommendation:")
    print(f"    BUY {shares}x BTC ${rec.strike:.2f} PUT (5% OTM, 21d)")
    print(f"    Premium:    ${rec.total_premium:.2f} ({rec.annual_cost_pct:.1f}% annualized)")
    print(f"    Delta:      {rec.delta:.3f}")
    print(f"    Breakeven:  ${rec.breakeven:.2f} ({(rec.breakeven/spot - 1)*100:.1f}%)")
    print(f"    Max loss:   ${rec.total_premium:.2f} (premium only)")
    print(f"    Protection: below ${rec.strike:.2f}, gains ${shares}/pt to B/E")
    print(f"{'='*65}\n")
