"""Opening report + per-level option value / position P/L estimates."""
from __future__ import annotations

from datetime import datetime

import config
from options_math import estimate_option_value, years_to_expiry

LEVELS = [("entry", "Entry"), ("stop", "Stop"), ("t1", "T1"), ("t2", "T2")]


def level_estimates(trade, S_now: float, opt_quote, now: datetime, trail_stop: float | None = None) -> dict:
    """{key: {price, option, pnl}} for entry/stop/T1/T2 (+ trailing stop if tightened)."""
    levels = {k: getattr(trade, k) for k, _ in LEVELS}
    if trail_stop is not None and trail_stop != trade.stop:
        levels["trail"] = trail_stop
    out = {}
    for k, price in levels.items():
        v = estimate_option_value(trade.contract, price, S_now, opt_quote.mark, opt_quote.iv, opt_quote.delta,
                                  now, config.RISK_FREE_RATE)
        out[k] = {"price": price, "option": v, "pnl": (v - trade.premium_paid) * trade.multiplier}
    return out


def opening_report(trade, S_now: float, opt_quote, now: datetime) -> tuple[dict, str]:
    est = level_estimates(trade, S_now, opt_quote, now)
    cost = trade.premium_paid * trade.multiplier
    planned_loss = est["stop"]["pnl"]
    gain1, gain2 = est["t1"]["pnl"], est["t2"]["pnl"]
    risk = abs(planned_loss) if planned_loss < 0 else None
    dte = years_to_expiry(trade.contract.expiry, now) * 365
    data = {
        "cost": cost, "max_loss": -cost, "planned_loss": planned_loss,
        "rr_t1": gain1 / risk if risk else None, "rr_t2": gain2 / risk if risk else None,
        "rr_t1_vs_max": gain1 / cost, "rr_t2_vs_max": gain2 / cost,
        "levels": est, "dte": dte,
        "mark": opt_quote.mark, "iv": opt_quote.iv, "delta": opt_quote.delta, "theta": opt_quote.theta,
        "theta_position_day": (opt_quote.theta or 0) * trade.multiplier,
        "underlying": S_now, "time": now.astimezone(config.ET).strftime("%Y-%m-%d %H:%M ET"),
    }
    f = lambda x: "n/a" if x is None else f"{x:,.2f}"          # noqa: E731
    sgn = lambda x: f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"   # noqa: E731
    lines = [
        "=" * 68,
        f" OPENING REPORT  {trade.id}  {'(PAPER)' if trade.paper else ''}   {data['time']}",
        "=" * 68,
        f" {trade.contracts} x {trade.contract.display} ({trade.direction.upper()})   timeframe {trade.timeframe}",
        f" Premium paid {trade.premium_paid:.2f}  ->  cost ${cost:,.0f}     DTE {dte:.1f}",
        f" Now: {trade.ticker} {S_now:.2f}  option mark {f(opt_quote.mark)}  IV "
        f"{(opt_quote.iv or 0) * 100:.1f}%  delta {f(opt_quote.delta)}  theta {f(opt_quote.theta)}"
        f" (${data['theta_position_day']:,.0f}/day position)",
        "",
        f" {'Level':<8}{'Underlying':>12}{'Est. option':>14}{'Position P/L':>16}",
    ]
    for k, label in LEVELS:
        e = est[k]
        lines.append(f" {label:<8}{e['price']:>12.2f}{e['option']:>14.2f}{sgn(e['pnl']):>16}")
    lines += [
        "",
        f" Planned loss at stop       {sgn(planned_loss)}",
        f" Absolute max loss (premium) {sgn(-cost)}",
        f" R:R to T1  {f(data['rr_t1'])} : 1   ({sgn(gain1)} vs {sgn(planned_loss)})",
        f" R:R to T2  {f(data['rr_t2'])} : 1   ({sgn(gain2)} vs {sgn(planned_loss)})",
        f" vs max loss: T1 {data['rr_t1_vs_max']:.2f} : 1, T2 {data['rr_t2_vs_max']:.2f} : 1",
        "",
        " Estimates reprice the contract at each level with current IV and time",
        " (as if hit now). Later hits will be worth less by theta; IV changes move them too.",
        "=" * 68,
    ]
    return data, "\n".join(lines)
