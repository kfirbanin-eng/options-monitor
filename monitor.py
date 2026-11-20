"""Options trade monitor - ALERT ONLY. Never places, modifies or cancels orders.

Usage
  python monitor.py trades/paper_spy_call.json --feed paper          # simulated session (no keys)
  python monitor.py trades/paper_spy_call.json --feed paper --speed 0 --no-web --auto-exit
  python monitor.py trades/my_trade.json --feed schwab               # live Schwab data
  python monitor.py trades/my_trade.json --feed yahoo                # live Yahoo underlying, modelled option price
  python monitor.py trades/replay_spy_734p_2026-11-19.json --feed replay --data data/spy_2026-11-19.json
  python monitor.py trades/my_trade.json --log-exit 7.85             # write journal row by hand

Loop (live): every QUOTE_POLL_SEC fetch underlying + option quote -> live P/L and level
checks; on each 5-minute candle close re-evaluate status on the trade timeframe;
every 15 minutes refresh the market panel. Desktop notifications fire only on a
status change (GREEN/YELLOW/RED) or a level hit.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import config
from feeds import ModelOptionFeed, PaperFeed, ReplayFeed, SchwabFeed, YahooFeed
from journal import write_exit_row
from market import market_panel
from notifier import TAGS, Notifier
from options_math import estimate_option_value
from report import level_estimates, opening_report
from rules import GREEN, RED, WAIT, YELLOW, check_levels, compute_indicators, evaluate, tighten
from schwab_client import ReadOnlyViolation
from trade import load_trade

LEVEL_NAMES = {"stop": "Original stop", "trail": "Trailing stop", "t1": "Target 1", "t2": "Target 2"}
ENDING_LEVELS = ("stop", "t2")   # plan over -> sticky RED. Trailing-stop hits alert but do not force RED.


def et_str(dt: datetime, fmt="%Y-%m-%d %H:%M") -> str:
    return dt.astimezone(config.ET).strftime(fmt)


def money(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}${abs(v):,.0f}"


def market_open(now: datetime) -> bool:
    et = now.astimezone(config.ET)
    return et.weekday() < 5 and time(9, 30) <= et.time() < time(16, 0)


def chart_time(dt: datetime) -> int:
    """Chart library works in UTC seconds; shift so the axis reads in ET."""
    return int(dt.timestamp() + dt.astimezone(config.ET).utcoffset().total_seconds())


class Monitor:
    def __init__(self, trade, feed, notifier: Notifier, *, state_dir=config.STATE_DIR,
                 journal_path=config.JOURNAL_FILE, report_dir=config.REPORT_DIR,
                 poll_sec: int | None = None, auto_exit=False, ignore_hours=False, fresh=False,
                 stop_when_done=False, heartbeat_min: int = 0, reminders: bool = False):
        self.trade, self.feed, self.notifier = trade, feed, notifier
        self.stop_when_done, self.heartbeat_min = stop_when_done, heartbeat_min
        self.last_heartbeat: datetime | None = None
        self.reminders, self.last_reminder = reminders, None
        self.seen_open = False
        self.clock = feed.clock
        self.journal_path, self.report_dir = Path(journal_path), Path(report_dir)
        self.poll_sec = poll_sec or (config.QUOTE_POLL_SEC if feed.live else 60)
        self.settle = config.CANDLE_SETTLE_SEC if feed.live else 0
        self.auto_exit, self.ignore_hours = auto_exit, ignore_hours
        self.opt_symbol = trade.contract.schwab_symbol
        suffix = "" if feed.live else ".sim"
        self.state_path = Path(state_dir) / f"{trade.id}{suffix}.json"
        self.state = self._load_state(fresh or not feed.live)
        self.lock = threading.Lock()
        self.exit_request: dict | None = None
        self.done = bool(self.state.get("closed"))
        self.last_eval_boundary: datetime | None = None
        self.last_eval_bar_end: datetime | None = None
        self.last_market: datetime | None = None
        self.under = self.opt = self.ev = self.market = None
        self.estimates: dict = {}
        self.chart: dict = {}
        self.note = ""
        self.errors: list[str] = []
        self.snapshot: dict = {}
        self.report_text = self.state.get("opening_text", "")
        self.journal_row = None

    # ---------------- state ----------------
    def _load_state(self, fresh: bool) -> dict:
        if not fresh and self.state_path.exists():
            st = json.loads(self.state_path.read_text())
            print(f"Resumed state from {self.state_path} (trailing stop {st.get('trail_stop')})")
            return st
        return {"trade_id": self.trade.id, "trail_stop": self.trade.stop, "status": None, "sticky_red": None,
                "levels_hit": {}, "status_history": [], "mfe": 0.0, "mae": 0.0, "closed": False}

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2, default=str))

    # ---------------- lifecycle ----------------
    def opening(self):
        now = self.clock.now()
        self.state.setdefault("opened_at", et_str(now))
        q = self.feed.quotes([self.trade.ticker, self.opt_symbol])
        self.under, self.opt = q[self.trade.ticker], q[self.opt_symbol]
        if "opening" not in self.state:
            data, text = opening_report(self.trade, self.under.last, self.opt, now)
            self.state["opening"], self.state["opening_text"] = data, text
            self.report_dir.mkdir(parents=True, exist_ok=True)
            (self.report_dir / f"{self.trade.id}_opening.txt").write_text(text + "\n")
            self.save()
        self.report_text = self.state["opening_text"]
        print(self.report_text)
        o = self.state["opening"]
        lv = o["levels"]
        lines = [f"{self.trade.contracts} x {self.trade.contract.display} @ {self.trade.premium_paid:.2f} "
                 f"(cost ${o['cost']:,.0f})"]
        for k, label in (("stop", "Stop"), ("t1", "T1" if not self.trade.single_target else "Target"), ("t2", "T2")):
            if k in lv:
                lines.append(f"{label} {lv[k]['price']:.2f}: option ~{lv[k]['option']:.2f} ({money(lv[k]['pnl'])})")
        lines.append(f"Max loss {money(o['max_loss'])}" + (f" | R:R {o['rr_t1']:.1f}:1" if o.get("rr_t1") else ""))
        self.notifier.push(f"Monitoring started: {self.trade.contract.display}", "\n".join(lines),
                           priority="default", tags="eyes")

    def heartbeat(self, now: datetime):
        """Quiet periodic phone update (no sound): status, P/L, stop."""
        if not self.heartbeat_min or not self.opt or self.opt.mark is None:
            return
        if self.last_heartbeat and now - self.last_heartbeat < timedelta(minutes=self.heartbeat_min):
            return
        self.last_heartbeat = now
        st = self.state.get("status") or WAIT
        mkt = f"\nMarket: {self.market['summary']}" if self.market else ""
        self.notifier.push(f"{st} | P/L {self.pnl_text()} | {self.trade.contract.display}",
                           f"{et_str(now, '%H:%M')} ET  {self.trade.ticker} {self.under.last:.2f}  "
                           f"option {self.opt.mark:.2f}  stop {self.state['trail_stop']:.2f}{mkt}",
                           priority="low", tags=TAGS.get(st, "chart_with_upwards_trend"))

    def reminder(self, now: datetime):
        """Mindset check 1 minute before each 15m candle closes - once per quarter hour."""
        et = now.astimezone(config.ET)
        if not self.reminders or not config.reminder_due(et):
            return
        key = et.strftime("%Y-%m-%d %H:%M")
        if key == self.last_reminder:
            return
        self.last_reminder = key
        self.notifier.push(config.REMINDER_TITLE, config.REMINDER_TEXT, priority="default", tags="hourglass",
                           topic=config.NTFY_REMINDER_TOPIC)
        self.notifier.speak(config.REMINDER_TEXT)

    def finish(self, now: datetime, reason: str):
        """Stop monitoring and send a final phone summary."""
        if self.done:
            return
        self.done = True
        hits = ", ".join(f"{k.upper()} {v['time']}" for k, v in self.state["levels_hit"].items()) or "none"
        mark = f"{self.opt.mark:.2f}" if self.opt and self.opt.mark is not None else "n/a"
        self.notifier.push(f"Monitoring ended: {self.trade.contract.display}",
                           f"{reason}\nLast option {mark}, P/L {self.pnl_text()} "
                           f"(best {money(self.state['mfe'])}, worst {money(self.state['mae'])})\n"
                           f"Levels hit: {hits}\nStatus {self.state.get('status')}",
                           priority="default", tags="checkered_flag")
        self.save()

    def run(self, max_ticks: int | None = None):
        if not self.done:
            self.opening()
        ticks = 0
        while not self.done:
            now = self.clock.now()
            if self.feed.live and not self.ignore_hours and not market_open(now):
                if self.stop_when_done and (self.seen_open or now.astimezone(config.ET).time() >= time(16, 0)):
                    self.finish(now, "Market closed")
                    break
                self.note = f"Market closed ({et_str(now, '%a %H:%M ET')}) - waiting for 09:30 ET"
                self.publish(now)
                self.handle_exit_request(now)
                self.clock.sleep(60)
                continue
            self.note = ""
            self.seen_open = True
            try:
                self.tick(now)
                self.heartbeat(now)
                self.reminder(now)
            except ReadOnlyViolation:
                raise
            except Exception as e:  # a bad poll must never kill the monitor
                msg = f"{et_str(now)} {type(e).__name__}: {e}"
                self.errors = (self.errors + [msg])[-10:]
                print("ERROR", msg)
                traceback.print_exc()
            self.handle_exit_request(now)
            if self.stop_when_done and self.state.get("sticky_red") and not self.done:
                self.finish(now, self.state["sticky_red"])
            if not self.feed.live and self.feed.finished() and not self.done:
                if self.auto_exit:
                    self.log_exit(None, "paper session end", now)
                self.finish(now, "Session ended")
            self.publish(now)
            ticks += 1
            if max_ticks and ticks >= max_ticks:
                break
            if not self.done:
                self.clock.sleep(self.poll_sec)
        self.publish(self.clock.now())

    def tick(self, now: datetime):
        q = self.feed.quotes([self.trade.ticker, self.opt_symbol])
        self.under, self.opt = q[self.trade.ticker], q[self.opt_symbol]
        price = self.under.last
        if self.opt.mark is not None:
            pnl = (self.opt.mark - self.trade.premium_paid) * self.trade.multiplier
            self.state["mfe"] = max(self.state["mfe"], pnl)
            self.state["mae"] = min(self.state["mae"], pnl)
        if price is not None:
            self.handle_hits(check_levels(self.trade, self.state["trail_stop"], price, price), price, now)

        boundary = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % config.EVAL_EVERY_MIN)
        if self.last_eval_boundary is None or (boundary > self.last_eval_boundary
                                               and now >= boundary + timedelta(seconds=self.settle)):
            self.evaluate(now)
            self.last_eval_boundary = boundary
        if self.last_market is None or now - self.last_market >= timedelta(minutes=config.MARKET_EVERY_MIN):
            self.market = market_panel(self.feed, self.trade.side, now, self.state.get("prev_vix"))
            self.state["prev_vix"] = self.market["vix"].get("last")
            self.last_market = now
        if price is not None:
            self.estimates = level_estimates(self.trade, price, self.opt, now, self.state["trail_stop"])

    def evaluate(self, now: datetime):
        t = self.trade
        bars = self.feed.bars(t.ticker, t.tf_minutes, days=3)
        closed = [b for b in bars if b.end <= now]
        forming = [b for b in bars if b.end > now]
        if not closed:
            return
        # wicks since the last evaluation can hit levels between quote polls
        fresh = [b for b in closed if self.last_eval_bar_end is None or b.end > self.last_eval_bar_end]
        if self.last_eval_bar_end is None:
            fresh = closed[-1:]
        if fresh:
            hi, lo = max(b.high for b in fresh), min(b.low for b in fresh)
            self.handle_hits(check_levels(t, self.state["trail_stop"], hi, lo), fresh[-1].close, now)
        self.last_eval_bar_end = closed[-1].end

        ev = evaluate(t, closed, self.state.get("sticky_red"))
        old_trail = self.state["trail_stop"]
        if ev.candidate_stop is not None and ev.status in (GREEN, YELLOW):
            self.state["trail_stop"] = tighten(t.side, old_trail, ev.candidate_stop, ev.close)
            if self.state["trail_stop"] != old_trail:
                self.state["levels_hit"].pop("trail", None)   # new trailing level -> re-arm its alert
        self.ev = ev
        self.set_status(ev.status, ev, now)
        self.build_chart(closed, forming)
        self.save()
        trail_note = f"  stop {old_trail:.2f}->{self.state['trail_stop']:.2f}" if self.state["trail_stop"] != old_trail else ""
        print(f"[{et_str(now, '%H:%M')}] {ev.status:<6} close {ev.close or 0:.2f}  {ev.action}{trail_note}")

    def set_status(self, status: str, ev, now: datetime, notify: bool = True):
        prev = self.state.get("status")
        if status == prev or status == WAIT:
            return
        self.state["status"] = status
        reason = "; ".join(ev.reasons) if ev else ""
        self.state["status_history"].append({"time": et_str(now, "%H:%M"), "from": prev, "to": status,
                                             "reason": reason})
        if notify and prev not in (None, WAIT):
            mkt = f"\nMarket: {self.market['summary']}" if self.market else ""
            self.notifier.notify(
                f"{self.trade.contract.display}: {prev} -> {status}",
                f"{ev.action if ev else ''}\n{reason}\nStop now {self.state['trail_stop']:.2f} | "
                f"P/L {self.pnl_text()}{mkt}", now.astimezone(config.ET),
                priority="urgent" if status == RED else "high", tags=TAGS.get(status, "bell"))

    def handle_hits(self, hits: list[str], price: float, now: datetime):
        for key in hits:
            if key in self.state["levels_hit"]:
                continue
            level = self.state["trail_stop"] if key == "trail" else getattr(self.trade, key)
            self.state["levels_hit"][key] = {"time": et_str(now, "%H:%M"), "level": level, "price": price}
            name = "Target" if key == "t1" and self.trade.single_target else LEVEL_NAMES[key]
            at_level = None
            opt_txt = ""
            if self.opt and self.opt.mark:
                # what the contract is worth AT the level (a resting limit there would fill near this),
                # vs the mark now - they differ when price ran through the level between polls
                at_level = estimate_option_value(self.trade.contract, level, price, self.opt.mark, self.opt.iv,
                                                 self.opt.delta, now, config.RISK_FREE_RATE)
                lvl_pnl = (at_level - self.trade.premium_paid) * self.trade.multiplier
                opt_txt = (f"option at {level:.2f} ~{at_level:.2f} ({'+' if lvl_pnl >= 0 else '-'}${abs(lvl_pnl):,.0f}); "
                           f"mark now {self.opt.mark:.2f}, P/L {self.pnl_text()}")
            self.state["levels_hit"][key]["option_at_level"] = at_level
            ending = key in ENDING_LEVELS or (key == "t1" and self.trade.single_target)
            if ending:
                self.state["sticky_red"] = f"{name} {level:.2f} hit"
                advice = "Plan says exit (alert only - nothing is sent to the broker)."
                if self.state.get("status") != RED:
                    self.set_status(RED, None, now, notify=False)
                    self.state["status_history"][-1]["reason"] = self.state["sticky_red"]
                status_txt = " Status -> RED."
            elif key == "trail":
                self.state["trail_hits"] = self.state.get("trail_hits", 0) + 1
                advice, status_txt = "Suggested trailing stop broken - consider exiting or reducing.", ""
            else:
                advice, status_txt = "Consider taking partial profits and tightening the stop.", ""
            self.notifier.notify(f"{self.trade.contract.display}: {name} hit @ {level:.2f}",
                                 f"{self.trade.ticker} {price:.2f}; {opt_txt}.{status_txt} {advice}",
                                 now.astimezone(config.ET), priority="urgent" if ending else "high",
                                 tags="dart" if key in ("t1", "t2") else "warning")
            self.save()
            if ending and self.auto_exit and not self.feed.live:
                self.log_exit(at_level, f"paper auto-exit: {name} {level:.2f} hit (filled at level)", now)

    def pnl_text(self) -> str:
        if not self.opt or self.opt.mark is None:
            return "n/a"
        pnl = (self.opt.mark - self.trade.premium_paid) * self.trade.multiplier
        return f"{'+' if pnl >= 0 else '-'}${abs(pnl):,.0f}"

    # ---------------- exit / journal ----------------
    def request_exit(self, price: float | None, reason: str):
        with self.lock:
            self.exit_request = {"price": price, "reason": reason or "manual exit"}

    def handle_exit_request(self, now: datetime):
        with self.lock:
            req, self.exit_request = self.exit_request, None
        if req and not self.done:
            self.log_exit(req["price"], req["reason"], now)

    def log_exit(self, price: float | None, reason: str, now: datetime):
        if self.done:
            return
        exit_px = price if price is not None else (self.opt.mark if self.opt else None)
        if exit_px is None:
            print("No exit price available; not journaling.")
            return
        self.journal_row = write_exit_row(self.journal_path, self.trade, self.state, exit_px,
                                          self.under.last if self.under else None, et_str(now), reason)
        self.state["closed"] = True
        self.state["exit"] = self.journal_row
        self.save()
        self.done = True
        print(f"\nJournal row written to {self.journal_path}: exit {exit_px:.2f}, "
              f"P/L ${self.journal_row['pnl_usd']:,.2f} ({reason})")

    # ---------------- web snapshot ----------------
    def build_chart(self, closed, forming):
        ind = compute_indicators(closed)
        n = 160
        start = max(0, len(closed) - n)
        rows = closed[start:]
        series = lambda vals: [{"time": chart_time(b.start), "value": round(v, 4)}  # noqa: E731
                               for b, v in zip(rows, vals[start:]) if v is not None]
        self.chart = {
            "candles": [{"time": chart_time(b.start), "open": b.open, "high": b.high, "low": b.low, "close": b.close}
                        for b in rows + forming[-1:]],
            "ema8": series(ind.ema8), "ema13": series(ind.ema13), "ema21": series(ind.ema21),
            "psar": [{"time": chart_time(b.start), "value": round(v, 4), "long": lg}
                     for b, v, lg in zip(rows, ind.sar[start:], ind.sar_long[start:]) if v is not None],
        }

    def publish(self, now: datetime):
        t, o = self.trade, self.opt
        pos = None
        if o and o.mark is not None:
            pnl = (o.mark - t.premium_paid) * t.multiplier
            pos = {"value": o.mark * t.multiplier, "cost": t.premium_paid * t.multiplier, "pnl": pnl,
                   "pnl_pct": 100 * (o.mark / t.premium_paid - 1), "mfe": self.state["mfe"], "mae": self.state["mae"]}
        ev = self.ev
        snap = {
            "mode": "READ-ONLY - alerts only, no orders",
            "feed": getattr(self.feed, "label", "Schwab live" if self.feed.live else "Paper simulation"),
            "now": et_str(now, "%Y-%m-%d %H:%M:%S ET"), "note": self.note, "errors": self.errors,
            "trade": {"id": t.id, "paper": t.paper, "ticker": t.ticker, "contract": t.contract.display,
                      "direction": t.direction, "contracts": t.contracts, "premium_paid": t.premium_paid,
                      "timeframe": t.timeframe, "entry": t.entry, "stop": t.stop, "t1": t.t1, "t2": t.t2},
            "status": self.state.get("status") or WAIT,
            "eval": ev.__dict__ if ev else None,
            "trail_stop": self.state["trail_stop"],
            "levels": self.estimates, "levels_hit": self.state["levels_hit"],
            "underlying": self.under.last if self.under else None,
            "option": o.__dict__ if o else None, "position": pos,
            "market": self.market, "history": self.state["status_history"],
            "alerts": self.notifier.sent[-25:], "chart": self.chart,
            "opening": self.state.get("opening"), "opening_text": self.report_text,
            "closed": self.done and bool(self.state.get("closed")), "exit": self.state.get("exit"),
        }
        with self.lock:
            self.snapshot = snap

    def get_snapshot(self) -> dict:
        with self.lock:
            return self.snapshot


# ---------------------------------------------------------------------------
def build_feed(kind: str, trade, speed: float, seed: int, data: str | None = None, option_price: str | None = None):
    if kind == "paper":
        feed = PaperFeed(trade, seed=seed, speed=speed)
    elif kind == "replay":
        if not data:
            raise SystemExit("--feed replay needs --data <file>, e.g. --data data/spy_2026-11-19.json")
        feed = ReplayFeed(trade, data, speed=speed)
    elif kind == "yahoo":
        feed = YahooFeed()
    else:
        feed = None
    if feed is None:
        from schwab_client import SchwabClient
        client = SchwabClient(config.SCHWAB_APP_KEY, config.SCHWAB_APP_SECRET, config.TOKEN_FILE,
                              config.SCHWAB_CALLBACK_URL)
        feed = SchwabFeed(client)
    option_price = option_price or ("model" if kind == "yahoo" else "feed")
    if option_price == "model":
        today = feed.clock.now().astimezone(config.ET).date()
        feed = ModelOptionFeed(feed, trade, entered_at=trade.entry_time(today), iv=trade.iv)
        if feed.entered_at is None:
            print('  Tip: add "entered_at": "HH:MM" (ET) to the trade file so the model IV uses your real entry time.')
    return feed


def main(argv=None):
    ap = argparse.ArgumentParser(description="Alert-only options trade monitor (read-only).")
    ap.add_argument("trade", help="trade JSON file")
    ap.add_argument("--feed", choices=["schwab", "yahoo", "paper", "replay"], default="schwab")
    ap.add_argument("--option-price", choices=["feed", "model"],
                    help="model = Black-Scholes from your entry premium (default for yahoo); feed = market quote")
    ap.add_argument("--data", help="replay: recorded day file (see data/)")
    ap.add_argument("--speed", type=float, default=60, help="paper/replay: simulated seconds per real second (0 = max)")
    ap.add_argument("--seed", type=int, default=7, help="paper: random seed for the simulated path")
    ap.add_argument("--port", type=int, default=config.WEB_PORT)
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument("--no-desktop", action="store_true", help="console/log alerts only")
    ap.add_argument("--auto-exit", action="store_true", help="paper/replay: journal exit when stop/final target hit or session ends")
    ap.add_argument("--ignore-hours", action="store_true", help="live: evaluate outside 09:30-16:00 ET")
    ap.add_argument("--fresh", action="store_true", help="live: ignore saved state (resets trailing stop!)")
    ap.add_argument("--stop-when-done", action="store_true",
                    help="exit (with a phone summary) when stop/final target is hit or the market closes")
    ap.add_argument("--reminders", action="store_true",
                    help="mindset reminder 1 minute before every 15m candle closes (phone + console)")
    ap.add_argument("--heartbeat-min", type=int, default=0,
                    help="send a quiet P/L update to the phone every N minutes (needs NTFY_TOPIC)")
    ap.add_argument("--log-exit", type=float, metavar="PRICE", help="write journal row at this option price and quit")
    ap.add_argument("--exit-underlying", type=float)
    ap.add_argument("--reason", default="manual exit")
    args = ap.parse_args(argv)

    trade = load_trade(args.trade)
    print(f"\n  OPTIONS MONITOR  [{'READ-ONLY' if config.READ_ONLY else '?'}]  alerts only - never places, "
          f"modifies or cancels orders\n  trade {trade.id}{' (PAPER)' if trade.paper else ''}  feed {args.feed}\n")

    if args.log_exit is not None:
        state_path = config.STATE_DIR / f"{trade.id}.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {"trail_stop": trade.stop}
        row = write_exit_row(config.JOURNAL_FILE, trade, state, args.log_exit, args.exit_underlying,
                             et_str(datetime.now(timezone.utc)), args.reason)
        state.update(closed=True, exit=row)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, default=str))
        print(f"Journal row written to {config.JOURNAL_FILE}: P/L ${row['pnl_usd']:,.2f}")
        return

    feed = build_feed(args.feed, trade, args.speed, args.seed, args.data, args.option_price)
    notifier = Notifier(desktop=not args.no_desktop, log_path=config.ALERT_LOG,
                        ntfy_topic=config.NTFY_TOPIC, ntfy_server=config.NTFY_SERVER)
    if notifier.ntfy_topic:
        print("  Phone push notifications: on (ntfy)")
    mon = Monitor(trade, feed, notifier, auto_exit=args.auto_exit, ignore_hours=args.ignore_hours,
                  fresh=args.fresh, stop_when_done=args.stop_when_done, heartbeat_min=args.heartbeat_min,
                  reminders=args.reminders)
    if not args.no_web:
        from web import start_web
        start_web(mon, config.WEB_HOST, args.port)
        print(f"  Live page: http://{config.WEB_HOST}:{args.port}\n")
    try:
        mon.run()
        if not args.no_web:
            print("Monitoring finished. Page stays up - Ctrl+C to quit.")
            threading.Event().wait()
    except KeyboardInterrupt:
        mon.save()
        if not mon.done:
            print(f"\nStopped. State saved to {mon.state_path} (trailing stop kept). To journal the exit:\n"
                  f"  python monitor.py {args.trade} --log-exit <option price>")
        sys.exit(0)


if __name__ == "__main__":
    main()
