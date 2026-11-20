"""Record a trading day from Yahoo Finance into a replay file (5-minute bars).

    python tools/record_day.py SPY 2026-11-20            -> data/spy_2026-11-20.json

Includes the previous trading day of the ticker (indicator warm-up), plus QQQ/IWM/DIA/VIX
for the market panel. Has no option prices: the replay then uses the model option price.
Works during the day too (records what has traded so far). Yahoo keeps 5m bars ~60 days.
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from feeds import YahooFeed  # noqa: E402
from yahoo_client import YahooClient  # noqa: E402


def segment(bars):
    return {"t": [int(b.start.timestamp()) for b in bars], "o": [round(b.open, 4) for b in bars],
            "h": [round(b.high, 4) for b in bars], "l": [round(b.low, 4) for b in bars],
            "c": [round(b.close, 4) for b in bars]}


def main():
    ticker, day = sys.argv[1].upper(), date.fromisoformat(sys.argv[2])
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("data") / f"{ticker.lower()}_{day}.json"
    feed = YahooFeed(YahooClient(cache_sec=0))
    symbols = {}
    for sym in [ticker] + [s for s in config.MARKET_SYMBOLS if s != ticker] + [config.VIX_SYMBOL]:
        res = feed.client.chart(feed._sym(sym), "5m", "1mo")
        bars = feed._parse(res, 5)
        by_day = {}
        for b in bars:
            by_day.setdefault(b.start.astimezone(config.ET).date(), []).append(b)
        days = sorted(d for d in by_day if d <= day)
        if not days or days[-1] != day:
            raise SystemExit(f"Yahoo has no {sym} bars for {day}")
        keep = days[-2:] if sym == ticker else days[-1:]      # previous day only for the ticker
        symbols[sym] = [segment(by_day[d]) for d in keep]
        print(f"{sym:5} {', '.join(f'{d} ({len(by_day[d])} bars)' for d in keep)}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"description": f"{ticker} {day}, 5-minute bars from Yahoo Finance (no option prices)",
                               "interval_min": 5, "symbols": symbols}))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
