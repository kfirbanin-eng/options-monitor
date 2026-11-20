"""Data feeds. Both feeds expose the same interface so the monitor can't tell them apart:

    feed.clock.now() / feed.clock.sleep(sec)
    feed.bars(symbol, minutes)  -> list[Bar]   (may include the still-forming bar)
    feed.quotes(symbols)        -> dict[symbol, Quote]
    feed.finished()             -> bool        (paper session over)

SchwabFeed  - live data from the read-only Schwab client.
PaperFeed   - a deterministic simulated session (trend, T1, pullback, resume,
              breakdown) so every status and alert can be exercised in minutes,
              with no API keys and no market hours.
"""
from __future__ import annotations

import math
import random
import time as _time
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

import config
from options_math import bs_greeks, bs_price, years_to_expiry


@dataclass
class Bar:
    start: datetime   # tz-aware UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    minutes: int

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.minutes)


@dataclass
class Quote:
    symbol: str
    last: float | None = None
    open: float | None = None
    mark: float | None = None
    bid: float | None = None
    ask: float | None = None
    iv: float | None = None       # decimal, 0.18 == 18%
    delta: float | None = None
    theta: float | None = None    # $ per share per day
    gamma: float | None = None


class LiveClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, sec: float) -> None:
        _time.sleep(sec)


class SimClock:
    """Simulated time. speed = simulated seconds per real second (0 = no real waiting)."""

    def __init__(self, start: datetime, speed: float = 0):
        self._now, self.speed = start, speed

    def now(self) -> datetime:
        return self._now

    def sleep(self, sec: float) -> None:
        self._now += timedelta(seconds=sec)
        if self.speed > 0:
            _time.sleep(sec / self.speed)


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or v <= -999 else v


# ---------------------------------------------------------------------------
class SchwabFeed:
    live = True

    def __init__(self, client):
        self.client = client
        self.clock = LiveClock()

    def bars(self, symbol: str, minutes: int, days: int = 3) -> list[Bar]:
        data = self.client.price_history(symbol, minutes, days)
        return [Bar(datetime.fromtimestamp(c["datetime"] / 1000, tz=timezone.utc), c["open"], c["high"],
                    c["low"], c["close"], c.get("volume", 0), minutes) for c in data.get("candles", [])]

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        raw = self.client.quotes(symbols)
        out = {}
        for sym in symbols:
            q = (raw.get(sym) or raw.get(sym.strip()) or {}).get("quote", {})
            iv = _num(q.get("volatility"))
            out[sym] = Quote(sym, last=_num(q.get("lastPrice")), open=_num(q.get("openPrice")),
                             mark=_num(q.get("mark")), bid=_num(q.get("bidPrice")), ask=_num(q.get("askPrice")),
                             iv=iv / 100 if iv else None, delta=_num(q.get("delta")),
                             theta=_num(q.get("theta")), gamma=_num(q.get("gamma")))
            if out[sym].last is None:
                out[sym].last = out[sym].mark
        return out

    def finished(self) -> bool:
        return False


# ---------------------------------------------------------------------------
class PaperFeed:
    """Simulated session built around the trade's own levels.

    Script (fractions of the distance entry->T1 / T1->T2, mirrored for puts):
      prior day + 09:30-10:00  drift into entry   (warm-up history)
      10:00-11:05  steady trend toward T1          -> GREEN, trail to PSAR
      11:05-11:25  push through T1                 -> T1 level alert
      11:25-12:10  pullback under EMA8             -> YELLOW
      12:10-13:00  resume, new highs               -> GREEN, stop tightens
      13:00-14:00  breakdown through trail/EMA21   -> RED
      14:00-16:00  drift toward original stop
    """
    live = False

    def __init__(self, trade, seed: int = 7, speed: float = 0, day=None, iv: float = 0.18):
        self.trade, self.iv0 = trade, iv
        self.rng = random.Random(seed)
        day = day or datetime.now(config.ET).date()
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        self.day = day
        prev = day - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        self.session_open = datetime.combine(day, time(9, 30), tzinfo=config.ET).astimezone(timezone.utc)
        self.session_close = datetime.combine(day, time(16, 0), tzinfo=config.ET).astimezone(timezone.utc)
        prev_open = datetime.combine(prev, time(9, 30), tzinfo=config.ET).astimezone(timezone.utc)
        self.clock = SimClock(self.session_open + timedelta(minutes=30), speed)
        self._build(prev_open)

    # path helpers -------------------------------------------------------
    def _path(self, start_price: float, segments: list[tuple[int, float]], vol: float) -> list[float]:
        """segments = [(minutes, target_price)]; linear drift + mean-reverting noise."""
        out, p_base, noise = [], start_price, 0.0
        for mins, target in segments:
            for i in range(1, mins + 1):
                base = p_base + (target - p_base) * i / mins
                noise = 0.6 * noise + self.rng.gauss(0, vol)
                out.append(base + noise)
            p_base = target
        return out

    def _bars_from(self, closes: list[float], starts: list[datetime], vol: float) -> list[Bar]:
        bars, prev = [], closes[0]
        for c, st in zip(closes, starts):
            o = prev
            hi = max(o, c) + abs(self.rng.gauss(0, vol * 0.6))
            lo = min(o, c) - abs(self.rng.gauss(0, vol * 0.6))
            bars.append(Bar(st, o, hi, lo, c, self.rng.randint(5_000, 50_000), 1))
            prev = c
        return bars

    def _build(self, prev_open: datetime):
        t = self.trade
        E, T1, T2, S = t.entry, t.t1, t.t2, t.stop
        d1, d2 = T1 - E, T2 - T1
        vol = abs(d1) * 0.035
        warm = E - 0.5 * d1
        prev_day = self._path(E - 0.9 * d1, [(390, warm)], vol)
        today = self._path(warm, [
            (30, E),                    # 09:30-10:00 into entry
            (65, E + 0.85 * d1),        # 10:00-11:05 trend
            (20, T1 + 0.15 * d2),       # 11:05-11:25 T1
            (45, E + 0.45 * d1),        # 11:25-12:10 pullback
            (50, T1 + 0.45 * d2),       # 12:10-13:00 resume
            (60, E + 0.10 * d1),        # 13:00-14:00 breakdown
            (120, S + 0.30 * (E - S)),  # 14:00-16:00 drift toward stop
        ], vol)
        starts_prev = [prev_open + timedelta(minutes=i) for i in range(390)]
        starts_today = [self.session_open + timedelta(minutes=i) for i in range(390)]
        under = self._bars_from(prev_day + today, starts_prev + starts_today, vol)
        self.minute_bars = {t.ticker: under}

        # Market symbols: correlated with the underlying (or independent-ish), VIX inverse.
        ref = [b.close for b in under]
        r0 = ref[390]
        bases = {"SPY": 600.0, "QQQ": 520.0, "IWM": 230.0, "DIA": 440.0}
        betas = {"SPY": 1.0, "QQQ": 1.2, "IWM": 0.5, "DIA": 0.8}
        for sym, base in bases.items():
            if sym == t.ticker:
                continue
            drift, closes = 0.0, []
            for c in ref:
                drift = 0.98 * drift + self.rng.gauss(0, 0.0004)
                closes.append(base * (1 + betas[sym] * (c / r0 - 1) + drift))
            self.minute_bars[sym] = self._bars_from(closes, starts_prev + starts_today, base * 0.0003)
        vix = [16.0 * (1 - 6 * (c / r0 - 1)) + self.rng.gauss(0, 0.03) for c in ref]
        self.minute_bars[config.VIX_SYMBOL] = self._bars_from(vix, starts_prev + starts_today, 0.03)

    # feed interface -------------------------------------------------------
    def _visible(self, symbol: str) -> list[Bar]:
        now = self.clock.now()
        return [b for b in self.minute_bars[symbol] if b.start < now]

    def bars(self, symbol: str, minutes: int, days: int = 3) -> list[Bar]:
        one = self._visible(symbol)
        if minutes == 1:
            return one
        out: dict[datetime, Bar] = {}
        for b in one:
            et = b.start.astimezone(config.ET)
            key = b.start - timedelta(minutes=(et.minute - 30) % minutes if et.hour == 9 else et.minute % minutes)
            if key not in out:
                out[key] = Bar(key, b.open, b.high, b.low, b.close, b.volume, minutes)
            else:
                g = out[key]
                g.high, g.low, g.close, g.volume = max(g.high, b.high), min(g.low, b.low), b.close, g.volume + b.volume
        return list(out.values())

    def _day_open(self, symbol: str) -> float:
        return next(b.open for b in self.minute_bars[symbol] if b.start >= self.session_open)

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out = {}
        opt_sym = self.trade.contract.schwab_symbol
        for sym in symbols:
            if sym == opt_sym:
                out[sym] = self._option_quote()
                continue
            vis = self._visible(sym)
            last = vis[-1].close if vis else None
            out[sym] = Quote(sym, last=last, open=self._day_open(sym), mark=last)
        return out

    def _option_quote(self) -> Quote:
        t, c = self.trade, self.trade.contract
        S = self._visible(t.ticker)[-1].close
        now = self.clock.now()
        T = years_to_expiry(c.expiry, now)
        move = t.side * (S / t.entry - 1)
        iv = max(0.05, self.iv0 * (1 - 3 * move))   # IV drifts against the trade direction
        mark = bs_price(S, c.strike, T, iv, config.RISK_FREE_RATE, c.right)
        g = bs_greeks(S, c.strike, T, iv, config.RISK_FREE_RATE, c.right)
        spread = max(0.02, round(mark * 0.01, 2))
        return Quote(c.schwab_symbol, last=round(mark, 2), mark=round(mark, 2), bid=round(mark - spread / 2, 2),
                     ask=round(mark + spread / 2, 2), iv=iv, delta=g["delta"], theta=g["theta"], gamma=g["gamma"])

    def finished(self) -> bool:
        return self.clock.now() >= self.session_close
