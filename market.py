"""Market panel: do SPY/QQQ/IWM/DIA move with the trade, and which way is $VIX going?

"With the trade" = up for a call, down for a put, measured vs today's open
and vs the open of the current clock hour. VIX falling supports calls,
rising supports puts.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import config


def _hour_open(bars, now: datetime) -> float | None:
    et = now.astimezone(config.ET)
    hour_start = et.replace(minute=30 if et.hour == 9 else 0, second=0, microsecond=0)
    for start in (hour_start, hour_start - timedelta(hours=1)):   # fall back to the prior hour at :00 sharp
        for b in bars:
            if b.start.astimezone(config.ET) >= start:
                return b.open
    return None


def market_panel(feed, side: int, now: datetime, prev_vix: float | None = None) -> dict:
    syms = config.MARKET_SYMBOLS + [config.VIX_SYMBOL]
    quotes = feed.quotes(syms)
    rows, with_day, with_hour = [], 0, 0
    for sym in config.MARKET_SYMBOLS:
        q = quotes.get(sym)
        if not q or q.last is None:
            rows.append({"symbol": sym, "error": "no quote"})
            continue
        try:
            bars = [b for b in feed.bars(sym, 5, days=1) if b.start > now - timedelta(hours=2)]
        except Exception:
            bars = []
        h_open = _hour_open(bars, now)
        vs_day = (q.last - q.open) if q.open else None
        vs_hour = (q.last - h_open) if h_open else None
        wd = vs_day is not None and side * vs_day > 0
        wh = vs_hour is not None and side * vs_hour > 0
        with_day += wd
        with_hour += wh
        rows.append({"symbol": sym, "last": q.last,
                     "vs_day_pct": 100 * vs_day / q.open if vs_day is not None else None,
                     "vs_hour_pct": 100 * vs_hour / h_open if vs_hour is not None else None,
                     "with_day": wd, "with_hour": wh})

    vq = quotes.get(config.VIX_SYMBOL)
    vix = {"last": None}
    if vq and vq.last is not None:
        vs_open = vq.last - vq.open if vq.open else None
        since = vq.last - prev_vix if prev_vix is not None else None
        ref = since if since is not None else vs_open
        direction = "flat" if ref is None or abs(ref) < 0.05 else ("rising" if ref > 0 else "falling")
        vix = {"last": vq.last, "vs_open": vs_open, "since_last": since, "direction": direction,
               "supportive": (direction == "falling") if side > 0 else (direction == "rising")}
    n = len(config.MARKET_SYMBOLS)
    return {"rows": rows, "with_day": with_day, "with_hour": with_hour, "of": n, "vix": vix,
            "summary": f"{with_day}/{n} with trade vs day open, {with_hour}/{n} vs hour open; "
                       f"VIX {vix.get('direction', 'n/a')}",
            "updated": now.astimezone(config.ET).strftime("%H:%M")}
