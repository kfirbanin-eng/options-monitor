"""Run:  python -m unittest discover -s tests -v"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import schwab_client
from feeds import Bar, ModelOptionFeed, PaperFeed, ReplayFeed, YahooFeed
from indicators import ema, psar
from monitor import Monitor
from notifier import Notifier
from options_math import bs_price
from rules import GREEN, RED, YELLOW, check_levels, evaluate, tighten
from schwab_client import ReadOnlyViolation, check_allowed
from trade import Trade, load_trade, parse_contract

ROOT = Path(__file__).resolve().parents[1]


def mk_trade(direction="call", **kw):
    base = dict(ticker="SPY", contract_raw="SPY 2026-12-18 600 C", direction="call", contracts=2,
                premium_paid=10, entry=100, stop=98, t1=103, t2=106)
    if direction == "put":
        base.update(contract_raw="SPY 2026-12-18 600 P", direction="put", stop=102, t1=97, t2=94)
    base.update(kw)
    return Trade(**base)


def bars_from(closes, spread=0.1):
    t0 = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Bar(t0 + timedelta(minutes=5 * i), prev, max(prev, c) + spread, min(prev, c) - spread, c, 1, 5))
        prev = c
    return out


def mirror(bars, pivot=200):
    return [Bar(b.start, pivot - b.open, pivot - b.low, pivot - b.high, pivot - b.close, 1, 5) for b in bars]


class ReadOnly(unittest.TestCase):
    def test_market_data_get_allowed(self):
        check_allowed("GET", "https://api.schwabapi.com/marketdata/v1/quotes?symbols=SPY")
        check_allowed("POST", "https://api.schwabapi.com/v1/oauth/token")

    def test_orders_and_accounts_blocked(self):
        for m in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            for path in ("/trader/v1/accounts/abc/orders", "/trader/v1/accounts/abc/orders/1",
                         "/trader/v1/accounts", "/marketdata/../trader/v1/orders"):
                if m == "GET" and path.startswith("/marketdata"):
                    continue
                with self.assertRaises(ReadOnlyViolation, msg=f"{m} {path}"):
                    check_allowed(m, "https://api.schwabapi.com" + path)
        for m in ("POST", "PUT", "DELETE"):
            with self.assertRaises(ReadOnlyViolation):
                check_allowed(m, "https://api.schwabapi.com/marketdata/v1/quotes")
        with self.assertRaises(ReadOnlyViolation):
            check_allowed("GET", "https://evil.example.com/marketdata/v1/quotes")

    def test_session_enforces_guard_before_network(self):
        s = schwab_client.GuardedSession()
        with self.assertRaises(ReadOnlyViolation):
            s.post("https://api.schwabapi.com/trader/v1/accounts/x/orders", json={})
        with self.assertRaises(ReadOnlyViolation):
            s.delete("https://api.schwabapi.com/trader/v1/accounts/x/orders/1")

    def test_no_order_code_anywhere(self):
        for py in ROOT.glob("*.py"):
            src = py.read_text().lower()
            for word in ("place_order", "replace_order", "cancel_order", "/orders\""):
                self.assertNotIn(word, src, f"{py.name} mentions {word}")


    def test_path_traversal_and_tricks_blocked(self):
        for u in ("https://api.schwabapi.com/marketdata/v1/../../trader/v1/accounts/x/orders",
                  "https://api.schwabapi.com/marketdata/v1/%2e%2e/%2e%2e/trader/v1/accounts",
                  "https://api.schwabapi.com/marketdata/v1/%2F..%2Ftrader",
                  "https://api.schwabapi.com:8443/marketdata/v1/quotes",
                  "https://user:pw@api.schwabapi.com/marketdata/v1/quotes",
                  "http://api.schwabapi.com/marketdata/v1/quotes"):
            with self.assertRaises(ReadOnlyViolation, msg=u):
                check_allowed("GET", u)

    def test_redirects_disabled_and_send_guarded(self):
        import requests
        s = schwab_client.GuardedSession()
        req = requests.Request("GET", "https://api.schwabapi.com/trader/v1/accounts").prepare()
        with self.assertRaises(ReadOnlyViolation):
            s.send(req)


class WebHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socket
        from web import start_web

        class Fake:
            exits = []
            def get_snapshot(self): return {"ok": True}
            def request_exit(self, price, reason): self.exits.append((price, reason))
        with socket.socket() as so:
            so.bind(("127.0.0.1", 0)); cls.port = so.getsockname()[1]
        cls.mon = Fake()
        cls.srv = start_web(cls.mon, "127.0.0.1", cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, method="GET", path="/api/state", headers=None, body=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        return r.status, r.read(), r

    def test_normal_access(self):
        st, _, r = self.req()
        self.assertEqual(st, 200)
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))

    def test_dns_rebinding_blocked(self):
        self.assertEqual(self.req(headers={"Host": "evil.example.com"})[0], 403)
        self.assertEqual(self.req(headers={"Origin": "https://evil.example.com"})[0], 403)

    def test_exit_requires_header_json_and_small_body(self):
        ok = {"X-Monitor": "1", "Content-Type": "application/json"}
        self.assertEqual(self.req("POST", "/api/exit", {"Content-Type": "application/json"}, b"{}")[0], 403)
        self.assertEqual(self.req("POST", "/api/exit", {"X-Monitor": "1", "Content-Type": "text/plain"}, b"{}")[0], 403)
        self.assertEqual(self.req("POST", "/api/exit", ok, b"x" * 5000)[0], 413)
        self.assertEqual(self.req("POST", "/api/exit", ok, b'{"price": "nan"}')[0], 400)
        self.assertEqual(self.req("POST", "/api/exit", ok, b'{"price": 3.1, "reason": "t"}')[0], 200)
        self.assertEqual(self.mon.exits[-1], (3.1, "t"))

    def test_refuses_non_local_bind(self):
        from web import start_web
        with self.assertRaises(ValueError):
            start_web(self.mon, "0.0.0.0", 0)


class Indicators(unittest.TestCase):
    def test_ema(self):
        e = ema([1, 2, 3, 4, 5, 6], 3)
        self.assertEqual(e[:2], [None, None])
        self.assertAlmostEqual(e[2], 2.0)
        self.assertAlmostEqual(e[3], 3.0)
        self.assertAlmostEqual(e[5], 5.0)

    def test_psar_flips(self):
        up = [100 + i for i in range(15)]
        down = [114 - 2 * i for i in range(10)]
        closes = up + down
        sar, longs = psar([c + 0.5 for c in closes], [c - 0.5 for c in closes])
        self.assertTrue(longs[10])
        self.assertLess(sar[10], closes[10])
        self.assertFalse(longs[-1])
        self.assertGreater(sar[-1], closes[-1])


class Rules(unittest.TestCase):
    uptrend = [100 + 0.3 * i for i in range(40)]

    def test_green_call(self):
        ev = evaluate(mk_trade(), bars_from(self.uptrend))
        self.assertEqual(ev.status, GREEN)
        self.assertLess(ev.candidate_stop, ev.close)

    def test_yellow_call(self):
        closes = self.uptrend + [self.uptrend[-1] - 1.4]
        ev = evaluate(mk_trade(), bars_from(closes, spread=0.05))
        self.assertEqual(ev.status, YELLOW, ev.reasons)
        self.assertIn("taking half", ev.action)

    def test_red_call(self):
        closes = self.uptrend + [self.uptrend[-1] - 4]
        self.assertEqual(evaluate(mk_trade(), bars_from(closes)).status, RED)

    def test_put_mirror(self):
        for closes, want in ((self.uptrend, GREEN), (self.uptrend + [self.uptrend[-1] - 1.4], YELLOW),
                             (self.uptrend + [self.uptrend[-1] - 4], RED)):
            ev = evaluate(mk_trade("put"), mirror(bars_from(closes, spread=0.05)))
            self.assertEqual(ev.status, want, ev.reasons)

    def test_sticky_red(self):
        ev = evaluate(mk_trade(), bars_from(self.uptrend), sticky_red="Original stop 98.00 hit")
        self.assertEqual(ev.status, RED)

    def test_stop_only_tightens(self):
        self.assertEqual(tighten(1, 98, 99, 100), 99)      # call: higher = tighter
        self.assertEqual(tighten(1, 99, 98.5, 100), 99)    # never widen
        self.assertEqual(tighten(1, 99, 100.5, 100), 99)   # never through price
        self.assertEqual(tighten(-1, 102, 101, 100), 101)  # put: lower = tighter
        self.assertEqual(tighten(-1, 101, 101.5, 100), 101)
        self.assertEqual(tighten(-1, 101, 99.5, 100), 101)

    def test_levels(self):
        t = mk_trade()
        self.assertEqual(check_levels(t, 98, 101, 97.9), ["stop"])
        self.assertEqual(check_levels(t, 99.5, 103.2, 99.4), ["trail", "t1"])
        tp = mk_trade("put")
        self.assertEqual(check_levels(tp, 102, 102.1, 96.0), ["stop", "t1"])
        self.assertEqual(check_levels(tp, 102, 100, 93.9), ["t1", "t2"])


class TradeAndMath(unittest.TestCase):
    def test_contract_formats(self):
        a = parse_contract("SPY 2026-12-18 600 C")
        b = parse_contract("SPY   261218C00600000")
        self.assertEqual(a, b)
        self.assertEqual(a.schwab_symbol, "SPY   261218C00600000")

    def test_validation(self):
        with self.assertRaises(ValueError):
            mk_trade(stop=101)                        # call stop above entry
        with self.assertRaises(ValueError):
            mk_trade(contract_raw="SPY 2026-12-18 600 P")  # call with a put contract
        load_trade(ROOT / "trades" / "paper_spy_call.json")
        load_trade(ROOT / "trades" / "paper_qqq_put.json")

    def test_put_call_parity(self):
        S, K, T, v, r = 600, 590, 0.1, 0.2, 0.04
        import math
        self.assertAlmostEqual(bs_price(S, K, T, v, r, "C") - bs_price(S, K, T, v, r, "P"),
                               S - K * math.exp(-r * T), places=6)


class PaperSession(unittest.TestCase):
    def run_session(self, file):
        trade = load_trade(ROOT / "trades" / file)
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mon = Monitor(trade, PaperFeed(trade, speed=0), Notifier(desktop=False), state_dir=d,
                          journal_path=d / "j.csv", report_dir=d, auto_exit=True)
            trails = []
            orig = mon.evaluate
            mon.evaluate = lambda now: (orig(now), trails.append(mon.state["trail_stop"]))
            mon.run()
            return trade, mon, trails, (d / "j.csv").read_text()

    def test_full_paper_sessions(self):
        for f in ("paper_spy_call.json", "paper_qqq_put.json"):
            trade, mon, trails, journal = self.run_session(f)
            seen = {h["to"] for h in mon.state["status_history"]}
            self.assertTrue({GREEN, YELLOW, RED} <= seen, seen)
            self.assertIn("t1", mon.state["levels_hit"])
            s = trade.side
            self.assertTrue(all(s * (b - a) >= 0 for a, b in zip(trails, trails[1:])), "stop widened")
            self.assertIn(trade.id, journal)
            self.assertTrue(mon.done and mon.state["closed"])
            alerts = mon.notifier.sent
            self.assertTrue(all(("->" in a["title"]) or (" hit @ " in a["title"]) for a in alerts))
            snap = mon.get_snapshot()
            self.assertTrue(snap["chart"]["candles"] and snap["chart"]["psar"] and snap["market"])


class Replay(unittest.TestCase):
    def test_recorded_day_spy_734p(self):
        """Real 2026-11-19 SPY bars: the 723.15 target is hit and journaled at the level, not at the later mark."""
        trade = load_trade(ROOT / "trades" / "replay_spy_734p_2026-11-19.json")
        self.assertTrue(trade.single_target)
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            feed = ReplayFeed(trade, ROOT / "data" / "spy_2026-11-19.json")
            mon = Monitor(trade, feed, Notifier(desktop=False), state_dir=d, journal_path=d / "j.csv",
                          report_dir=d, auto_exit=True)
            mon.run()
            hit = mon.state["levels_hit"]
            self.assertEqual(hit["t1"]["time"], "12:05")
            self.assertIn("trail", hit)
            self.assertNotIn("stop", hit)
            self.assertAlmostEqual(mon.state["exit"]["exit_premium"], 10.85, places=2)
            seq = [h["to"] for h in mon.state["status_history"]]
            self.assertEqual(seq[:4], [RED, YELLOW, GREEN, YELLOW])

    def test_no_future_bars_visible(self):
        trade = load_trade(ROOT / "trades" / "replay_spy_734p_2026-11-19.json")
        feed = ReplayFeed(trade, ROOT / "data" / "spy_2026-11-19.json")
        now = feed.clock.now()
        self.assertTrue(all(b.end <= now for b in feed.bars("SPY", 5)))


class YahooAndModel(unittest.TestCase):
    def test_yahoo_guard(self):
        import yahoo_client
        yahoo_client.check_allowed("GET", "https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=5m")
        for m, u in (("POST", "https://query1.finance.yahoo.com/v8/finance/chart/SPY"),
                     ("GET", "https://query1.finance.yahoo.com/v7/finance/quote?symbols=SPY"),
                     ("GET", "https://query1.finance.yahoo.com/v8/finance/chart/../../v1/x"),
                     ("GET", "https://evil.example.com/v8/finance/chart/SPY")):
            with self.assertRaises(yahoo_client.YahooViolation, msg=u):
                yahoo_client.check_allowed(m, u)

    def test_yahoo_parsing(self):
        t0 = 1795098600
        class Fake:
            def chart(self, sym, interval, rng):
                self.last = sym
                return {"meta": {"regularMarketPrice": 735.0},
                        "timestamp": [t0, t0 + 300, t0 + 450, t0 + 600],
                        "indicators": {"quote": [{"open": [1, 2, 9, None], "high": [2, 3, 9, 4],
                                                  "low": [0.5, 1.5, 9, 3], "close": [1.5, 2.5, 9, 3.5],
                                                  "volume": [10, 20, 1, 5]}]}}
        fake = Fake()
        f = YahooFeed(client=fake)
        bars = f.bars("SPY", 5)
        self.assertEqual([b.close for b in bars], [1.5, 2.5])       # gap and off-grid point dropped
        f.bars("$VIX", 5)
        self.assertEqual(fake.last, "^VIX")
        self.assertEqual(f.quotes(["SPY"])["SPY"].last, 735.0)

    def test_model_price_tracks_real_option(self):
        """Model price vs the real 734P prices on 2026-11-19: mean error under $0.40/share."""
        t = load_trade(ROOT / "trades" / "replay_spy_734p_2026-11-19.json")
        data = ROOT / "data" / "spy_2026-11-19.json"
        real, base = ReplayFeed(t, data), ReplayFeed(t, data)
        m = ModelOptionFeed(base, t, entered_at=t.entry_time(base.clock.now().astimezone(base.clock.now().tzinfo).date()))
        sym, errs = t.contract.schwab_symbol, []
        while not base.finished():
            q = m.quotes([sym])[sym]
            self.assertTrue(q.model)
            errs.append(abs(q.mark - real.quotes([sym])[sym].mark))
            base.clock.sleep(300)
            real.clock.sleep(300)
        self.assertLess(sum(errs) / len(errs), 0.40)


class PhonePush(unittest.TestCase):
    def test_replay_with_phone_push_stops_when_done(self):
        sent = []

        class FakeHttp:
            def post(self, url, data, timeout, headers):
                sent.append((url, headers["Title"], headers["Priority"], data.decode()))
                return type("R", (), {"status_code": 200})()

        trade = load_trade(ROOT / "trades" / "replay_spy_734p_2026-11-19.json")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            n = Notifier(desktop=False, ntfy_topic="t0pic", ntfy_server="https://ntfy.example", http=FakeHttp())
            mon = Monitor(trade, ReplayFeed(trade, ROOT / "data" / "spy_2026-11-19.json"), n, state_dir=d,
                          journal_path=d / "j.csv", report_dir=d, stop_when_done=True, heartbeat_min=30)
            mon.run()
        titles = [t for _, t, _, _ in sent]
        self.assertTrue(all(u == "https://ntfy.example/t0pic" for u, _, _, _ in sent))
        self.assertTrue(titles[0].startswith("Monitoring started"))
        self.assertTrue(titles[-1].startswith("Monitoring ended"))
        self.assertIn("Target hit @ 723.15", " ".join(titles))
        self.assertEqual(mon.clock.now().astimezone(mon.clock.now().tzinfo).strftime("%H:%M"), "17:05")  # 12:05 ET
        urgent = [t for _, t, p, _ in sent if p == "urgent"]
        self.assertTrue(any("RED" in t for t in urgent) and any("Target" in t for t in urgent))

    def test_push_failure_never_raises(self):
        class Boom:
            def post(self, *a, **k):
                raise OSError("network down")
        Notifier(desktop=False, ntfy_topic="x", http=Boom()).notify("t", "m")


class Reminders(unittest.TestCase):
    def test_reminder_minutes(self):
        import config
        from datetime import datetime as dt
        due = lambda h, m: config.reminder_due(dt(2026, 11, 20, h, m, tzinfo=config.ET))  # a Friday
        self.assertTrue(all(due(10, m) for m in (14, 29, 44, 59)))
        self.assertFalse(any(due(10, m) for m in (0, 13, 15, 30)))
        self.assertFalse(due(9, 14))                       # before the open
        self.assertTrue(due(15, 59))
        self.assertFalse(config.reminder_due(dt(2026, 11, 21, 10, 14, tzinfo=config.ET)))  # Saturday

    def test_next_slot(self):
        import config
        from datetime import datetime as dt
        sys.path.insert(0, str(ROOT / "tools"))
        from reminders import next_slot
        self.assertEqual(next_slot(dt(2026, 11, 20, 10, 14, 30, tzinfo=config.ET)).strftime("%H:%M"), "10:29")
        self.assertEqual(next_slot(dt(2026, 11, 20, 9, 0, tzinfo=config.ET)).strftime("%H:%M"), "09:44")

    def test_replay_sends_reminders_once_per_quarter(self):
        trade = load_trade(ROOT / "trades" / "replay_spy_734p_2026-11-19.json")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            n = Notifier(desktop=False)
            pushed = []
            n.push = lambda title, msg, **k: pushed.append((mon.clock.now(), title, msg))
            mon = Monitor(trade, ReplayFeed(trade, ROOT / "data" / "spy_2026-11-19.json"), n, state_dir=d,
                          journal_path=d / "j.csv", report_dir=d, stop_when_done=True, reminders=True)
            mon.run()
        import config
        times = [t.astimezone(config.ET).strftime("%H:%M") for t, title, _ in pushed if title == config.REMINDER_TITLE]
        self.assertEqual(times, ["10:29", "10:44", "10:59", "11:14", "11:29", "11:44", "11:59"])
        self.assertIn("What do you know to be true?", pushed[1][2])


if __name__ == "__main__":
    unittest.main()
