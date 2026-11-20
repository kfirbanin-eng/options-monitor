"""Central configuration. Everything tunable lives here or in .env."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# ---------------------------------------------------------------------------
# SAFETY: this program is an ALERT-ONLY monitor. There is no code anywhere in
# the project that places, modifies or cancels orders, and the HTTP layer in
# schwab_client.py refuses any request outside the read-only market-data API.
# This flag exists only so the banner / web page can show the mode; it cannot
# be turned off because there is nothing to turn on.
# ---------------------------------------------------------------------------
READ_ONLY = True

# Schwab Trader API credentials (developer.schwab.com app)
SCHWAB_APP_KEY = os.getenv("SCHWAB_APP_KEY", "")
SCHWAB_APP_SECRET = os.getenv("SCHWAB_APP_SECRET", "")
SCHWAB_CALLBACK_URL = os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1")
TOKEN_FILE = ROOT / os.getenv("SCHWAB_TOKEN_FILE", "tokens.json")

ET = ZoneInfo("America/New_York")

# Strategy parameters
EMA_PERIODS = (8, 13, 21)
PSAR_STEP = 0.02
PSAR_MAX = 0.2
EVAL_EVERY_MIN = 5            # status is re-evaluated on each 5-minute candle close
MARKET_EVERY_MIN = 15         # SPY/QQQ/IWM/DIA/$VIX refresh
MARKET_SYMBOLS = ["SPY", "QQQ", "IWM", "DIA"]
VIX_SYMBOL = "$VIX"
RISK_FREE_RATE = float(os.getenv("RISK_FREE_RATE", "0.04"))

# Loop timing (live feed)
QUOTE_POLL_SEC = int(os.getenv("QUOTE_POLL_SEC", "20"))  # live quote / level check
CANDLE_SETTLE_SEC = 5         # wait this long after a candle closes before fetching it

# Local web page (bound to localhost only)
WEB_HOST = "127.0.0.1"
WEB_PORT = int(os.getenv("MONITOR_PORT", "8765"))

# Phone push notifications via ntfy (https://ntfy.sh) - optional
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")
# Optional separate topic for the 15-minute reminders, so the phone can play a recorded voice for them only
NTFY_REMINDER_TOPIC = os.getenv("NTFY_REMINDER_TOPIC", "") or NTFY_TOPIC

# Mindset reminder, sent 1 minute before every 15-minute candle closes (:14, :29, :44, :59 ET)
REMINDER_TITLE = "15m candle closes in 1 minute"
REMINDER_TEXT = ("First of all, focus.\n"
                 "Deep breath.\n"
                 "What do you know to be true?\n"
                 "Check your chart.\n"
                 "Deep breath - do you need to step away?")


def reminder_due(now_et) -> bool:
    """True during the minute before a 15-minute candle closes (regular session only)."""
    t = now_et.hour * 60 + now_et.minute
    return now_et.weekday() < 5 and 9 * 60 + 30 <= t < 16 * 60 and now_et.minute % 15 == 14


# Output locations
STATE_DIR = ROOT / "state"
JOURNAL_FILE = ROOT / "journal" / "journal.csv"
REPORT_DIR = ROOT / "reports"
ALERT_LOG = ROOT / "logs" / "alerts.log"
