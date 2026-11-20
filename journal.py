"""Trade journal: one CSV row per closed trade (written when you log the exit)."""
from __future__ import annotations

import csv
from pathlib import Path

COLUMNS = [
    "trade_id", "paper", "ticker", "contract", "direction", "contracts", "timeframe",
    "opened_at", "closed_at", "entry_underlying", "exit_underlying",
    "premium_paid", "exit_premium", "pnl_usd", "pnl_pct",
    "original_stop", "final_trail_stop", "t1", "t2", "t1_hit", "t2_hit", "stop_hit", "trail_hit",
    "planned_loss_usd", "max_loss_usd", "rr_t1", "max_favorable_usd", "max_adverse_usd",
    "last_status", "status_changes", "exit_reason", "notes",
]


def write_exit_row(path: Path, trade, state: dict, exit_premium: float, exit_underlying: float | None,
                   closed_at: str, reason: str) -> dict:
    pnl = (exit_premium - trade.premium_paid) * trade.multiplier
    hits = state.get("levels_hit", {})
    rep = state.get("opening", {})
    row = {
        "trade_id": trade.id, "paper": trade.paper, "ticker": trade.ticker, "contract": trade.contract.display,
        "direction": trade.direction, "contracts": trade.contracts, "timeframe": trade.timeframe,
        "opened_at": state.get("opened_at", ""), "closed_at": closed_at,
        "entry_underlying": trade.entry, "exit_underlying": round(exit_underlying, 2) if exit_underlying else "",
        "premium_paid": trade.premium_paid, "exit_premium": round(exit_premium, 2),
        "pnl_usd": round(pnl, 2), "pnl_pct": round(100 * (exit_premium / trade.premium_paid - 1), 1),
        "original_stop": trade.stop, "final_trail_stop": round(state.get("trail_stop", trade.stop), 2),
        "t1": trade.t1, "t2": trade.t2,
        "t1_hit": "t1" in hits, "t2_hit": "t2" in hits, "stop_hit": "stop" in hits, "trail_hit": state.get("trail_hits", 0),
        "planned_loss_usd": round(rep.get("planned_loss", 0), 2), "max_loss_usd": round(rep.get("max_loss", 0), 2),
        "rr_t1": round(rep["rr_t1"], 2) if rep.get("rr_t1") else "",
        "max_favorable_usd": round(state.get("mfe", 0), 2), "max_adverse_usd": round(state.get("mae", 0), 2),
        "last_status": state.get("status", ""), "status_changes": len(state.get("status_history", [])),
        "exit_reason": reason, "notes": trade.notes,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerow(row)
    return row
