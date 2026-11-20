"""Status engine: EMA 8/13/21 + PSAR -> GREEN / YELLOW / RED, stop suggestions,
level-hit detection. Written once for calls; puts are mirrored with
trade.side (+1 call, -1 put): "above" means "in the trade's favour".

Rules (call; put is the mirror image):
  RED     close < EMA21
          or PSAR flipped against the trade (above price) and close < EMA13
          or a plan level was hit: original stop, trailing stop, or final target T2
          (level-based RED is sticky - the plan says the trade is over)
  GREEN   close > EMA8 and EMA8 > EMA13 > EMA21 and PSAR below price
          -> suggest trailing the stop to PSAR
  YELLOW  close < EMA8 but still > EMA13 and > EMA21
          -> suggest stop to the nearer of EMA13 / PSAR, consider taking half
  YELLOW  (mixed) anything else - trend not cleanly aligned either way

Stops only tighten: a suggestion is applied to the trailing stop only if it is
closer to price than the current trailing stop (higher for calls, lower for
puts) and still on the safe side of the close.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config
from indicators import ema, psar

GREEN, YELLOW, RED, WAIT = "GREEN", "YELLOW", "RED", "WAIT"


@dataclass
class Evaluation:
    status: str
    reasons: list[str]
    action: str
    close: float | None = None
    ema8: float | None = None
    ema13: float | None = None
    ema21: float | None = None
    psar: float | None = None
    psar_with_trade: bool | None = None
    candidate_stop: float | None = None
    bar_time: str | None = None


@dataclass
class Indicators:
    ema8: list
    ema13: list
    ema21: list
    sar: list
    sar_long: list


def compute_indicators(bars) -> Indicators:
    closes = [b.close for b in bars]
    p8, p13, p21 = config.EMA_PERIODS
    sar, sar_long = psar([b.high for b in bars], [b.low for b in bars], config.PSAR_STEP, config.PSAR_MAX)
    return Indicators(ema(closes, p8), ema(closes, p13), ema(closes, p21), sar, sar_long)


def tighten(side: int, current: float, candidate: float | None, close: float) -> float:
    """Return the new trailing stop. Never widens; ignores candidates at/through price."""
    if candidate is None:
        return current
    if side * (close - candidate) <= 0:        # candidate is on the wrong side of price
        return current
    return candidate if side * (candidate - current) > 0 else current


def check_levels(trade, trail_stop: float, hi: float, lo: float) -> list[str]:
    """Which plan levels did a price range [lo, hi] touch? Keys: stop, trail, t1, t2."""
    s = trade.side
    adverse = lo if s > 0 else hi      # worst price for the trade
    favour = hi if s > 0 else lo       # best price for the trade
    hits = []
    if s * (adverse - trade.stop) <= 0:
        hits.append("stop")
    elif trail_stop != trade.stop and s * (adverse - trail_stop) <= 0:
        hits.append("trail")
    if s * (favour - trade.t1) >= 0:
        hits.append("t1")
    if s * (favour - trade.t2) >= 0:
        hits.append("t2")
    return hits


def evaluate(trade, closed_bars, sticky_red: str | None = None) -> Evaluation:
    """Status for the latest *closed* bar of the trade timeframe."""
    s = trade.side
    if len(closed_bars) < max(config.EMA_PERIODS) + 1:
        return Evaluation(WAIT, [f"need {max(config.EMA_PERIODS) + 1} closed bars, have {len(closed_bars)}"],
                          "Waiting for indicator warm-up")
    ind = compute_indicators(closed_bars)
    c = closed_bars[-1].close
    e8, e13, e21 = ind.ema8[-1], ind.ema13[-1], ind.ema21[-1]
    sar = ind.sar[-1]
    sar_with = ind.sar_long[-1] == (s > 0)          # PSAR on the supportive side of price
    flipped_now = ind.sar_long[-1] != ind.sar_long[-2]
    above = lambda a, b: s * (a - b) > 0           # noqa: E731  "a is in the trade's favour vs b"
    word = "above" if s > 0 else "below"
    under = "below" if s > 0 else "above"
    ev = Evaluation(WAIT, [], "", c, e8, e13, e21, sar, sar_with,
                    bar_time=closed_bars[-1].start.astimezone(config.ET).strftime("%H:%M"))

    red = []
    if sticky_red:
        red.append(sticky_red)
    if not above(c, e21):
        red.append(f"close {c:.2f} {under} EMA21 {e21:.2f}")
    if not sar_with and not above(c, e13):
        red.append(f"PSAR {'flipped' if flipped_now else 'is'} against the trade ({sar:.2f}) and close {under} EMA13 {e13:.2f}")
    if red:
        ev.status, ev.reasons = RED, red
        ev.action = "Exit / close per plan (alert only - no orders are sent)"
        return ev

    if above(c, e8) and above(e8, e13) and above(e13, e21) and sar_with:
        ev.status = GREEN
        ev.reasons = [f"close {word} EMA8 {e8:.2f}", f"EMA8 {word} EMA13 {word} EMA21", f"PSAR {under} price at {sar:.2f}"]
        ev.candidate_stop = sar
        ev.action = f"Trend intact - trail stop to PSAR {sar:.2f}"
        return ev

    if not above(c, e8) and above(c, e13) and above(c, e21):
        ev.status = YELLOW
        ev.reasons = [f"close {c:.2f} {under} EMA8 {e8:.2f}", f"still {word} EMA13 {e13:.2f} and EMA21 {e21:.2f}"]
        options = [e13] + ([sar] if sar_with else [])
        nearer = max(options) if s > 0 else min(options)
        ev.candidate_stop = nearer
        src = "PSAR" if nearer == sar and sar_with else "EMA13"
        ev.action = f"Momentum fading - stop to {src} {nearer:.2f}, consider taking half"
        return ev

    ev.status = YELLOW
    ev.reasons = ["trend not cleanly aligned (EMA stack or PSAR mixed)"]
    if not sar_with:
        ev.reasons.append(f"PSAR {word} price at {sar:.2f}")
    ev.action = "Mixed signals - hold stop, watch closely"
    return ev
