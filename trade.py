"""Trade file loading and validation.

A trade file is JSON, e.g. trades/paper_spy_call.json:

{
  "id": "spy-600c-paper",          optional, defaults to file name
  "paper": true,                    paper = hypothetical position
  "ticker": "SPY",
  "contract": "SPY 2026-12-18 600 C",   or OCC "SPY   261218C00600000"
  "direction": "call",              call | put
  "contracts": 2,
  "premium_paid": 9.60,             per share (x100 per contract)
  "entry": 598.50,                  underlying prices from here down
  "stop": 595.80,
  "t1": 602.00,
  "t2": 605.50,
  "timeframe": "5m",                1m | 5m
  "notes": "..."
}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_OCC = re.compile(r"^([A-Z][A-Z0-9.]{0,5})\s*(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")
_FRIENDLY = re.compile(r"^([A-Z][A-Z0-9.]{0,5})\s+(\d{4})-(\d{2})-(\d{2})\s+(\d+(?:\.\d+)?)\s*([CP])(?:ALL|UT)?$")


@dataclass(frozen=True)
class Contract:
    root: str
    expiry: date
    right: str          # "C" or "P"
    strike: float

    @property
    def schwab_symbol(self) -> str:
        """Schwab/OCC 21-char symbol: root padded to 6, YYMMDD, C/P, strike*1000 (8 digits)."""
        return f"{self.root:<6}{self.expiry:%y%m%d}{self.right}{int(round(self.strike * 1000)):08d}"

    @property
    def display(self) -> str:
        return f"{self.root} {self.expiry:%Y-%m-%d} {self.strike:g}{self.right}"


def parse_contract(text: str) -> Contract:
    s = " ".join(text.strip().upper().split())
    m = _OCC.match(s.replace(" ", "")) or _OCC.match(s)
    if m:
        root, yy, mm, dd, right, strike = m.groups()
        return Contract(root, date(2000 + int(yy), int(mm), int(dd)), right, int(strike) / 1000.0)
    m = _FRIENDLY.match(s)
    if m:
        root, y, mo, d, strike, right = m.groups()
        return Contract(root, date(int(y), int(mo), int(d)), right, float(strike))
    raise ValueError(f"Unrecognised contract '{text}'. Use 'SPY 2026-12-18 600 C' or OCC 'SPY   261218C00600000'.")


@dataclass
class Trade:
    ticker: str
    contract_raw: str
    direction: str
    contracts: int
    premium_paid: float
    entry: float
    stop: float
    t1: float
    t2: float
    timeframe: str = "5m"
    paper: bool = False
    id: str = ""
    notes: str = ""
    contract: Contract = field(init=False)

    def __post_init__(self):
        self.ticker = self.ticker.upper().strip()
        self.direction = self.direction.lower().strip()
        self.contract = parse_contract(self.contract_raw)
        self.validate()

    @property
    def side(self) -> int:
        """+1 for calls (we want price up), -1 for puts. All rules are written once and mirrored with this."""
        return 1 if self.direction == "call" else -1

    @property
    def tf_minutes(self) -> int:
        return int(self.timeframe.rstrip("m"))

    @property
    def multiplier(self) -> int:
        return 100 * self.contracts

    def validate(self):
        errs = []
        if self.direction not in ("call", "put"):
            errs.append("direction must be 'call' or 'put'")
        elif (self.direction == "call") != (self.contract.right == "C"):
            errs.append(f"direction '{self.direction}' does not match contract right '{self.contract.right}'")
        if self.timeframe not in ("1m", "5m"):
            errs.append("timeframe must be '1m' or '5m'")
        if self.contracts <= 0:
            errs.append("contracts must be > 0")
        if self.premium_paid <= 0:
            errs.append("premium_paid must be > 0")
        s = self.side
        if not s * (self.entry - self.stop) > 0:
            errs.append(f"stop must be {'below' if s > 0 else 'above'} entry for a {self.direction}")
        if not s * (self.t1 - self.entry) > 0:
            errs.append(f"t1 must be {'above' if s > 0 else 'below'} entry for a {self.direction}")
        if not s * (self.t2 - self.t1) > 0:
            errs.append(f"t2 must be beyond t1 for a {self.direction}")
        if errs:
            raise ValueError("Invalid trade file:\n  - " + "\n  - ".join(errs))


def load_trade(path: str | Path) -> Trade:
    path = Path(path)
    d = json.loads(path.read_text())
    return Trade(
        ticker=d["ticker"],
        contract_raw=d["contract"],
        direction=d.get("direction") or d.get("type") or d["call_put"],
        contracts=int(d["contracts"]),
        premium_paid=float(d["premium_paid"]),
        entry=float(d["entry"]),
        stop=float(d["stop"]),
        t1=float(d["t1"]),
        t2=float(d["t2"]),
        timeframe=d.get("timeframe", "5m"),
        paper=bool(d.get("paper", False)),
        id=d.get("id") or path.stem,
        notes=d.get("notes", ""),
    )
