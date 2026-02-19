"""
Options-based hedging simulation.

Provides pure-Python Black-Scholes pricing (no scipy) and simulates
protective put and collar strategies with monthly rolling.
"""

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


# ── Black-Scholes (pure Python) ──────────────────────────────────────────


def _norm_cdf(x: float) -> float:
    """
    Standard normal CDF using Abramowitz & Stegun approximation (7.1.26).
    Max error ~1.5e-7.
    """
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911

    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2.0)
    return 0.5 * (1.0 + sign * y)


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes European put price."""
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes European call price."""
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


# ── Data structures ──────────────────────────────────────────────────────


class OptionsStrategyType(Enum):
    PROTECTIVE_PUT = "PROTECTIVE_PUT"
    COLLAR = "COLLAR"


@dataclass
class OptionRoll:
    """Tracks one monthly options roll period."""
    roll_date: str              # Date when new options were written/bought
    expiry_bar_idx: int         # Bar index when these options expire
    put_strike: float
    put_premium_paid: float     # Per-share premium paid for the put
    put_intrinsic_at_expiry: float = 0.0  # Per-share intrinsic value at settlement
    call_strike: Optional[float] = None   # Only for COLLAR
    call_premium_received: Optional[float] = None  # Per-share premium received
    call_intrinsic_at_expiry: float = 0.0  # Per-share intrinsic (liability) at settlement
    shares: int = 100           # Number of shares hedged


@dataclass
class OptionsDailySnapshot:
    """Daily mark-to-market for the options strategy."""
    date: str
    underlying_price: float
    equity_value: float         # shares * price
    put_mark: float             # Current BS value of held put (per-share * shares)
    call_mark: float            # Current BS value of short call (per-share * shares, liability)
    cumulative_premium_paid: float      # Total put premiums paid to date
    cumulative_premium_received: float  # Total call premiums received to date
    cumulative_put_intrinsic: float     # Total put intrinsic recovered at settlements
    cumulative_call_intrinsic: float    # Total call intrinsic paid at settlements
    net_portfolio_value: float  # equity + put_mark - call_mark - net_premiums + net_intrinsic


@dataclass
class OptionsSimulationResult:
    """Full result from an options strategy simulation."""
    symbol: str
    strategy_type: OptionsStrategyType
    snapshots: List[OptionsDailySnapshot]
    rolls: List[OptionRoll]
    initial_shares: int
    initial_price: float
    iv: float
    otm_pct: float
    roll_period_days: int


# ── Simulation ───────────────────────────────────────────────────────────


def run_options_simulation(
    bars: List[Dict],
    symbol: str,
    strategy_type: OptionsStrategyType,
    initial_shares: int,
    initial_price: float,
    iv: float = 0.20,
    otm_pct: float = 0.05,
    roll_period_days: int = 21,
    risk_free_rate: float = 0.05,
) -> OptionsSimulationResult:
    """
    Run an options hedging simulation over daily bars.

    Args:
        bars: Daily OHLCV bars in chronological order
        symbol: Underlying symbol (SPY, IWM)
        strategy_type: PROTECTIVE_PUT or COLLAR
        initial_shares: Number of shares to hedge (e.g. 100 = 1 contract)
        initial_price: Price at start for return calculation
        iv: Implied volatility (annualized)
        otm_pct: How far OTM to place strikes (0.05 = 5%)
        roll_period_days: Trading days between rolls (21 ~ 1 month)
        risk_free_rate: Risk-free rate for BS pricing

    Returns:
        OptionsSimulationResult with daily snapshots and roll history
    """
    rolls: List[OptionRoll] = []
    snapshots: List[OptionsDailySnapshot] = []

    # Cumulative tracking
    cum_premium_paid = 0.0
    cum_premium_received = 0.0
    cum_put_intrinsic = 0.0
    cum_call_intrinsic = 0.0

    # Current active roll
    active_roll: Optional[OptionRoll] = None
    next_roll_idx = 0  # Roll on the very first bar

    shares = initial_shares

    for bar_idx, bar in enumerate(bars):
        date = bar["date"]
        price = bar["close"]

        # ── Settle expiring options ──────────────────────────────────
        if active_roll is not None and bar_idx >= active_roll.expiry_bar_idx:
            # Put intrinsic at expiry
            put_intrinsic = max(active_roll.put_strike - price, 0.0)
            active_roll.put_intrinsic_at_expiry = put_intrinsic
            cum_put_intrinsic += put_intrinsic * active_roll.shares

            # Call intrinsic at expiry (collar only — this is a liability)
            if strategy_type == OptionsStrategyType.COLLAR and active_roll.call_strike is not None:
                call_intrinsic = max(price - active_roll.call_strike, 0.0)
                active_roll.call_intrinsic_at_expiry = call_intrinsic
                cum_call_intrinsic += call_intrinsic * active_roll.shares

            active_roll = None
            next_roll_idx = bar_idx  # Roll immediately after settlement

        # ── Open new roll ────────────────────────────────────────────
        if bar_idx >= next_roll_idx and active_roll is None:
            T = roll_period_days / 252.0  # Time to expiry in years

            put_strike = round(price * (1.0 - otm_pct), 2)
            put_prem = bs_put_price(price, put_strike, T, risk_free_rate, iv)
            cum_premium_paid += put_prem * shares

            call_strike = None
            call_prem = None
            if strategy_type == OptionsStrategyType.COLLAR:
                call_strike = round(price * (1.0 + otm_pct), 2)
                call_prem = bs_call_price(price, call_strike, T, risk_free_rate, iv)
                cum_premium_received += call_prem * shares

            roll = OptionRoll(
                roll_date=date,
                expiry_bar_idx=bar_idx + roll_period_days,
                put_strike=put_strike,
                put_premium_paid=put_prem,
                call_strike=call_strike,
                call_premium_received=call_prem,
                shares=shares,
            )
            rolls.append(roll)
            active_roll = roll
            next_roll_idx = bar_idx + roll_period_days

        # ── Daily mark-to-market ─────────────────────────────────────
        put_mark_total = 0.0
        call_mark_total = 0.0

        if active_roll is not None:
            remaining_bars = active_roll.expiry_bar_idx - bar_idx
            T_remaining = max(remaining_bars / 252.0, 1.0 / 252.0)  # At least 1 day

            put_mark_per_share = bs_put_price(
                price, active_roll.put_strike, T_remaining, risk_free_rate, iv
            )
            put_mark_total = put_mark_per_share * active_roll.shares

            if strategy_type == OptionsStrategyType.COLLAR and active_roll.call_strike is not None:
                call_mark_per_share = bs_call_price(
                    price, active_roll.call_strike, T_remaining, risk_free_rate, iv
                )
                call_mark_total = call_mark_per_share * active_roll.shares

        equity_value = shares * price

        # Net portfolio value:
        # equity + put mark (asset) - call mark (liability)
        # - cumulative premiums paid + cumulative premiums received
        # + cumulative put intrinsic recovered - cumulative call intrinsic paid
        net_value = (
            equity_value
            + put_mark_total
            - call_mark_total
            - cum_premium_paid
            + cum_premium_received
            + cum_put_intrinsic
            - cum_call_intrinsic
        )

        snapshots.append(OptionsDailySnapshot(
            date=date,
            underlying_price=price,
            equity_value=equity_value,
            put_mark=put_mark_total,
            call_mark=call_mark_total,
            cumulative_premium_paid=cum_premium_paid,
            cumulative_premium_received=cum_premium_received,
            cumulative_put_intrinsic=cum_put_intrinsic,
            cumulative_call_intrinsic=cum_call_intrinsic,
            net_portfolio_value=net_value,
        ))

    return OptionsSimulationResult(
        symbol=symbol,
        strategy_type=strategy_type,
        snapshots=snapshots,
        rolls=rolls,
        initial_shares=initial_shares,
        initial_price=initial_price,
        iv=iv,
        otm_pct=otm_pct,
        roll_period_days=roll_period_days,
    )
