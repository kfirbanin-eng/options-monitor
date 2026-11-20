# Options Trade Monitor (alert-only, read-only)

Watches one option trade, scores it GREEN / YELLOW / RED from EMA 8/13/21 and Parabolic SAR
on the underlying, and alerts you. **It never places, modifies or cancels orders.** It has no
order code, and its HTTP layer refuses every Schwab request except market-data GETs and the
OAuth token refresh.

## Phone (Android/iPhone): trade on the go, no computer

GitHub runs the monitor; your phone gets the alerts. Free (GitHub Actions minutes).

One-time setup:
1. Install the **ntfy** app and subscribe to a long random topic name (it works like a password).
2. In this repo on GitHub: Settings > Secrets and variables > Actions > New repository secret,
   name `NTFY_TOPIC`, value = that topic.
3. Keep the repo **private**: run logs show your trade details.

Each trade: GitHub app > this repo > Actions > **Monitor trade** > Run workflow > fill in the form > Run.
You get: a start message with value at stop/targets, alerts on status changes and level hits
(RED and stop/target are urgent), a silent P/L update every 15 minutes, and an end summary.
It stops by itself when the stop or final target is hit or the market closes; or cancel the run.
Tick **demo** once to test the setup any time: it replays 2026-11-19 in about 2 minutes.

Mindset reminder, 1 minute before every 15-minute candle closes (:14, :29, :44, :59 ET):
"First of all, focus. / Deep breath. / What do you know to be true? / Check your chart. /
Deep breath - do you need to step away?" (spoken on the PC in a calm, slow voice with pauses)
It is on during every monitored trade (phone workflow and `start.bat`). For the whole session without
a trade, run **Trading reminders** from the Actions tab before the open (stops at 16:00 ET).

Spoken: on the PC the reminder is also said out loud (Windows built-in voice) whenever the monitor runs
with `--reminders` (`start.bat`), or all session with `reminders.bat`. On the phone, put the reminders on
their own topic (`NTFY_REMINDER_TOPIC` secret), subscribe to it in ntfy, and set a recording of your
own voice as that subscription's notification sound.

Prices come from Yahoo Finance and the option price is the Black-Scholes model (see below).
The same phone alerts work from the PC: put `NTFY_TOPIC=...` in `.env`.

## Windows: double-click setup

1. Install Python from https://www.python.org/downloads/ and tick **Add python.exe to PATH**.
2. Download this repo as a ZIP (green **Code** button, **Download ZIP**) and right-click it, then **Extract All**.
3. In the extracted folder, double-click `install.bat` (once per computer).
4. Double-click `demo.bat` to check that everything works. It replays a real day, at any hour.
5. Live day: create `trades\live.json` with your trade, then double-click `start.bat`.

## Quick start

```bash
pip install -r requirements.txt
```

1. Paper test (no keys, simulated session, about 6 minutes at speed 60). Open http://127.0.0.1:8765

```bash
python monitor.py trades/paper_spy_call.json --feed paper
```

2. Same thing instantly, console only, journal row written at the end:

```bash
python monitor.py trades/paper_spy_call.json --feed paper --speed 0 --no-web --auto-exit
```

3. Run the tests:

```bash
python -m unittest discover -s tests -v
```

4. Live data. Copy `.env.example` to `.env`, then authorize once (repeat every 7 days):

```bash
python auth.py
```

5. Monitor a paper trade on live Schwab data, then a real trade:

```bash
python monitor.py trades/paper_spy_call.json --feed schwab
```

```bash
python monitor.py trades/my_trade.json --feed schwab
```

## No broker keys? Yahoo + model option price

```bash
python monitor.py trades/my_trade.json --feed yahoo
```

The underlying, SPY/QQQ/IWM/DIA and VIX come from Yahoo Finance (free, no account). The option's price
is **calculated**, not quoted: IV is backed out of `premium_paid` at your `entry` price at `entered_at`
(add `"entered_at": "HH:MM"` in ET to the trade file), then scaled with VIX, and the contract is priced
with Black-Scholes. The page labels it "(model)".

Checked against the real SPY 734P prices on 2026-11-19: mean error $0.21 per share (about $21 per contract),
worst $1.01. Treat it as a good estimate, not a fill price - spreads on fast days are wider.
Any feed can use the model with `--option-price model` (e.g. the replay, to compare).

## Replay a real day

`--feed replay` plays back a recorded day bar by bar, as if it were live: same rules, alerts and page.
Only candles that had closed at that moment are visible. Included: SPY on 2026-11-19 with the 0DTE 734 put
(real 5-minute prices from TradingView), and a trade file for a put entered on the break of 735.44, target 723.15.

```bash
python monitor.py trades/replay_spy_734p_2026-11-19.json --feed replay --data data/spy_2026-11-19.json
```

Open http://127.0.0.1:8765. Add `--speed 0 --no-web --auto-exit` for an instant console run.
With 5-minute data, level hits are seen at the candle close; the alert also shows the estimated option
value *at* the level, which is what a resting limit order there would have filled near.

## Rules (call; put is the mirror)

| Status | Condition | Suggestion |
|---|---|---|
| GREEN | close > EMA8, EMA8 > EMA13 > EMA21, PSAR below price | trail stop to PSAR |
| YELLOW | close < EMA8 but > EMA13 and > EMA21 | stop to nearer of EMA13/PSAR, consider taking half |
| YELLOW (mixed) | anything else that isn't RED | hold stop |
| RED | close < EMA21; or PSAR above price and close < EMA13; or original stop / T2 hit (sticky) | exit per plan |

* Status is evaluated on closed candles of the trade timeframe at each 5-minute boundary.
* Stops only tighten. A suggestion is applied only if it is closer to price and still on the safe side.
* Levels (stop, trailing stop, T1, T2) are checked on every quote poll (20 s) and on candle wicks.
  T1 hit gives a "take partial" alert. A trailing-stop hit alerts, and the alert re-arms when the stop tightens again.
* Desktop notifications fire only on a status change or a level hit.

## Files

| File | Purpose |
|---|---|
| `monitor.py` | Entry point and main loop: polls, evaluates, alerts, persists state, feeds the web page, journals the exit |
| `rules.py` | The status engine: GREEN/YELLOW/RED, stop suggestions, tighten-only logic, level-hit detection |
| `indicators.py` | EMA (SMA-seeded) and Wilder Parabolic SAR (0.02/0.2) in pure Python |
| `options_math.py` | Black-Scholes price/greeks/IV, and the "option value if the underlying hits X" estimate |
| `report.py` | Opening report: value and P/L at each level, planned loss, max loss, R:R in $ |
| `market.py` | Market panel: SPY/QQQ/IWM/DIA with the trade vs day/hour open, VIX direction |
| `schwab_client.py` | Read-only Schwab client with the request guard (`GuardedSession`) |
| `yahoo_client.py` | Read-only Yahoo Finance chart client (GET-only guard), used by `--feed yahoo` |
| `auth.py` | One-time OAuth login that saves `tokens.json` |
| `feeds.py` | `SchwabFeed` (live), `PaperFeed` (simulated session), `ReplayFeed` (recorded real day), `YahooFeed`, and `ModelOptionFeed` (Black-Scholes option price), behind one interface |
| `data/*.json` | Recorded days for replay mode |
| `notifier.py` | Desktop notifications (plyer / osascript / notify-send) plus `logs/alerts.log` |
| `journal.py` | Appends one CSV row per closed trade to `journal/journal.csv` |
| `web.py` + `static/index.html` | Local page on 127.0.0.1: chart, EMAs, PSAR dots, labelled level lines, panels, exit form |
| `config.py` | All settings; reads `.env` |
| `trades/*.json` | Trade files: two paper trades, the 2026-11-19 SPY replay, and `TEMPLATE.json`. `t2` is optional (single target) |
| `tests/test_monitor.py` | Read-only guard, indicators, rules incl. put mirror, tighten-only, full paper sessions |

Runtime output: `state/<id>.json` (trailing stop and history, resumed on restart so the stop never resets),
`reports/<id>_opening.txt`, `logs/alerts.log`, `journal/journal.csv`.

## Logging the exit

When you close the position at your broker, record it in one of two ways:

* Use the **Log exit** form on the page (leave the price blank to use the current mark).
* Run this from the command line:

```bash
python monitor.py trades/my_trade.json --log-exit 14.20 --reason "T1 partial + trail"
```

## Caveats

* Level option values reprice at current IV "as if hit now"; real fills drift with theta and IV.
* Exchange holidays aren't detected (the loop just sees no new bars).
* Schwab refresh tokens expire after 7 days. When that happens the monitor tells you to rerun `auth.py`.
