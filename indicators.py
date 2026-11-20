"""Pure-python indicators: EMA and Wilder's Parabolic SAR."""
from __future__ import annotations


def ema(values: list[float], period: int) -> list[float | None]:
    """EMA seeded with the SMA of the first `period` values (None before that)."""
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def psar(high: list[float], low: list[float], step: float = 0.02, max_af: float = 0.2):
    """Parabolic SAR.

    Returns (sar, is_long) lists. is_long[i] True means SAR is below price
    (bullish); False means SAR is above price (bearish).
    """
    n = len(high)
    sar: list[float | None] = [None] * n
    longs: list[bool | None] = [None] * n
    if n < 2:
        return sar, longs

    is_long = high[1] + low[1] >= high[0] + low[0]
    af = step
    if is_long:
        cur, ep = low[0], high[0]
    else:
        cur, ep = high[0], low[0]
    sar[0], longs[0] = cur, is_long

    for i in range(1, n):
        cur = cur + af * (ep - cur)
        if is_long:
            # SAR may not be above the prior two lows
            cur = min(cur, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < cur:                      # reversal to short
                is_long, cur, ep, af = False, ep, low[i], step
            elif high[i] > ep:
                ep, af = high[i], min(af + step, max_af)
        else:
            cur = max(cur, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if high[i] > cur:                     # reversal to long
                is_long, cur, ep, af = True, ep, high[i], step
            elif low[i] < ep:
                ep, af = low[i], min(af + step, max_af)
        sar[i], longs[i] = cur, is_long
    return sar, longs
