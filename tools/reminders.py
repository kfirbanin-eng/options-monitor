"""Standalone mindset reminders for the whole session (no trade needed).

Sends, 1 minute before every 15-minute candle closes (09:44 ... 15:59 ET):
  What do you know to be true? / Check your chart. / Deep breath - do you need to step away?
Runs until 16:00 ET. Needs NTFY_TOPIC (env or .env).
"""
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from notifier import Notifier  # noqa: E402


def next_slot(now_et: datetime) -> datetime:
    """Next HH:14/29/44/59 at :00 seconds, inside 09:30-16:00 on a weekday."""
    t = now_et.replace(second=0, microsecond=0)
    for _ in range(7 * 24 * 4 + 1):
        t += timedelta(minutes=1)
        if config.reminder_due(t):
            return t
    raise RuntimeError("no slot found")


def main():
    n = Notifier(desktop=False, ntfy_topic=config.NTFY_TOPIC, ntfy_server=config.NTFY_SERVER)
    if not n.ntfy_topic:
        raise SystemExit("Set NTFY_TOPIC first (GitHub secret, or .env on the PC).")
    now = datetime.now(config.ET)
    n.push("Reminders on", f"Every 15 minutes until 16:00 ET, 1 minute before the candle closes.\n"
                           f"First one at {next_slot(now):%H:%M} ET.", tags="hourglass")
    while True:
        now = datetime.now(config.ET)
        if now.weekday() >= 5 or now.time() >= datetime.strptime("16:00", "%H:%M").time():
            n.push("Reminders off", "Session over.", priority="low", tags="checkered_flag")
            return
        slot = next_slot(now)
        if slot.date() != now.date():
            n.push("Reminders off", "Session over.", priority="low", tags="checkered_flag")
            return
        time.sleep(max(0.0, (slot - datetime.now(config.ET)).total_seconds()))
        n.push(config.REMINDER_TITLE, config.REMINDER_TEXT, tags="hourglass")
        time.sleep(61)


if __name__ == "__main__":
    main()
