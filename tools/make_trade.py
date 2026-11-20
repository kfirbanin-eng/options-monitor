"""Build a trade file from the phone form (GitHub Actions inputs passed as env vars).

    CONTRACT="SPY 2026-11-20 734 P" PREMIUM=2.34 CONTRACTS=1 ENTRY=735.44 STOP=737.50 \
    TARGET1=723.15 TARGET2= ENTERED_AT= TIMEFRAME=5m  python tools/make_trade.py trades/phone.json

Ticker and call/put come from the contract. Blank ENTERED_AT = now (New York time).
Validates the trade exactly like the monitor does, so a typo fails here with a clear message.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import ET  # noqa: E402
from trade import load_trade, parse_contract  # noqa: E402


def env(name: str, required: bool = True) -> str:
    v = (os.environ.get(name) or "").strip()
    if required and not v:
        raise SystemExit(f"Missing field: {name}")
    return v


def number(name: str, required: bool = True):
    v = env(name, required)
    if not v:
        return None
    try:
        return float(v.replace(",", "").replace("$", ""))
    except ValueError:
        raise SystemExit(f"{name} must be a number, got '{v}'")


def main(out: str):
    c = parse_contract(env("CONTRACT"))
    entered = env("ENTERED_AT", False) or datetime.now(ET).strftime("%H:%M")
    trade = {
        "id": f"phone-{c.root}-{c.strike:g}{c.right}-{datetime.now(ET):%Y%m%d-%H%M}",
        "paper": False,
        "ticker": c.root,
        "contract": c.display,
        "direction": "call" if c.right == "C" else "put",
        "contracts": int(number("CONTRACTS")),
        "premium_paid": number("PREMIUM"),
        "entry": number("ENTRY"),
        "stop": number("STOP"),
        "t1": number("TARGET1"),
        "t2": number("TARGET2", False),
        "timeframe": env("TIMEFRAME", False) or "5m",
        "entered_at": entered,
    }
    Path(out).write_text(json.dumps(trade, indent=2))
    t = load_trade(out)       # same validation as the monitor
    print(f"Trade OK: {t.contracts} x {t.contract.display} @ {t.premium_paid}, entry {t.entry}, stop {t.stop}, "
          f"target {t.t1}" + ("" if t.single_target else f"/{t.t2}") + f", entered {entered} ET")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "trades/phone.json")
