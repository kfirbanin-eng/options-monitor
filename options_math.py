"""Black-Scholes helpers used to estimate what the option will be worth if the
underlying reaches a level (entry / stop / T1 / T2).

Method: reprice the contract with Black-Scholes at the target underlying price
using the contract's *current* IV and time to expiry ("if the level were hit
now"), then add the gap between the live mark and the model price at the
current underlying, so the estimate is anchored to the real market price.
Falls back to IV implied from the mark, then to a delta approximation.
"""
from __future__ import annotations

import math
from datetime import datetime, time

from config import ET

SECONDS_PER_YEAR = 365.0 * 24 * 3600


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def intrinsic(S: float, K: float, right: str) -> float:
    return max(0.0, S - K) if right == "C" else max(0.0, K - S)


def years_to_expiry(expiry, now: datetime) -> float:
    exp_dt = datetime.combine(expiry, time(16, 0), tzinfo=ET)
    return max((exp_dt - now).total_seconds() / SECONDS_PER_YEAR, 1e-6)


def bs_price(S: float, K: float, T: float, sigma: float, r: float, right: str) -> float:
    if T <= 1e-6 or sigma <= 0:
        return intrinsic(S, K, right)
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vt
    d2 = d1 - vt
    if right == "C":
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def bs_greeks(S: float, K: float, T: float, sigma: float, r: float, right: str) -> dict:
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vt
    d2 = d1 - vt
    gamma = _npdf(d1) / (S * vt)
    common = -S * _npdf(d1) * sigma / (2 * math.sqrt(T))
    if right == "C":
        delta = _ncdf(d1)
        theta = common - r * K * math.exp(-r * T) * _ncdf(d2)
    else:
        delta = _ncdf(d1) - 1
        theta = common + r * K * math.exp(-r * T) * _ncdf(-d2)
    return {"delta": delta, "gamma": gamma, "theta": theta / 365.0}


def implied_vol(price: float, S: float, K: float, T: float, r: float, right: str) -> float | None:
    if price <= intrinsic(S, K, right) + 1e-4:
        return None
    lo, hi = 0.005, 5.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, mid, r, right) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def estimate_option_value(contract, S_target: float, S_now: float, mark_now: float | None,
                          iv_now: float | None, delta_now: float | None, now: datetime, r: float) -> float:
    """Estimated option price (per share) if the underlying were at S_target now."""
    K, right = contract.strike, contract.right
    T = years_to_expiry(contract.expiry, now)
    floor = intrinsic(S_target, K, right)
    if not mark_now or mark_now <= 0:
        sigma = iv_now
        if sigma and sigma > 0:
            return max(bs_price(S_target, K, T, sigma, r, right), floor)
        return floor
    sigma = iv_now if iv_now and iv_now > 0 else implied_vol(mark_now, S_now, K, T, r, right)
    if sigma:
        offset = mark_now - bs_price(S_now, K, T, sigma, r, right)
        value = bs_price(S_target, K, T, sigma, r, right) + offset
    elif delta_now is not None:
        value = mark_now + delta_now * (S_target - S_now)
    else:
        value = floor
    return max(value, floor, 0.0)
