"""Pure-Python Black-Scholes pricing (no scipy dependency).

Ported from allocation-gym options module.
"""

import math


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via Abramowitz & Stegun (7.1.26). Max err ~1.5e-7."""
    a1, a2, a3, a4, a5 = (
        0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429,
    )
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(
        -x * x / 2.0
    )
    return 0.5 * (1.0 + sign * y)


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes European put price."""
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes European call price."""
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def bs_delta_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes put delta."""
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return -1.0 if S < K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1) - 1.0


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes gamma (same for put and call)."""
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return math.exp(-d1 * d1 / 2.0) / (S * sigma * math.sqrt(2 * math.pi * T))
