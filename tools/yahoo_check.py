"""Quick check that Yahoo Finance answers from this machine; reports to the phone too."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from feeds import YahooFeed  # noqa: E402
from notifier import Notifier  # noqa: E402

n = Notifier(desktop=False, ntfy_topic=config.NTFY_TOPIC, ntfy_server=config.NTFY_SERVER)
try:
    f = YahooFeed()
    q = f.quotes(["SPY", "$VIX"])
    bars = f.bars("SPY", 5)
    last = bars[-1].start.astimezone(config.ET).strftime("%Y-%m-%d %H:%M") if bars else "none"
    n.push("Yahoo check OK", f"SPY {q['SPY'].last}  VIX {q['$VIX'].last}  last 5m candle {last} ET", tags="white_check_mark")
except Exception as e:
    n.push("Yahoo check FAILED", f"{type(e).__name__}: {e}", priority="high", tags="x")
    sys.exit(1)
