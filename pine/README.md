# TradingView scripts

Both are alert-only indicators (no orders). Add them from Indicators > My scripts.

## Strat Range Setups (`strat_range.pine`)

Real-time Strat reversals at the edge of the range, on 15 / 30 / 60 min candles (daily optional).

- **Strat candles:** 1 = inside, 2U = takes only the prior high, 2D = takes only the prior low, 3 = outside.
- **Range lines:** top line through the higher highs, bottom line through the lower lows (broadening
  formation). Each line touches the extreme pivot of the look-back window and is drawn so no other pivot
  pokes through it. R / S tags show today's resistance / support.
- **Long at the bottom** (trigger = price takes out the setup candle's high):
  2D-1-2U, 3-1-2U, 1-2D-2U RevStrat, 2D-2U, 3-2U, Hammer-2U.
  **Short at the top** (trigger = price takes out the setup candle's low): the mirror.
- **READY** fires when a setup candle closes at the edge (inside / outside / RevStrat / hammer / shooter).
  **TRIGGER** fires the moment price breaks the trigger, without waiting for the candle to close.
- Each alert gives the trigger, stop (other side of the setup candle), T1 (previous candle's high/low),
  T2 (opposite range line) and timeframe continuity (price vs the M / W / D / 60 open).
- Filters: edge zone %, touch tolerance, cooldown, optional continuations, optional continuity filter.
- Alert setup: one alert per ticker, condition "Strat Range Setups" > "Any alert() function call".

## Options Monitor Status (`trade_status.pine`)

The monitor's EMA 8/13/21 + PSAR rules on the chart (trend both ways, or one trade's call/put status),
the same range lines, and shooter / hammer at the top / bottom of the range on 15 / 30 / 60 min candles.

## Strat Range Backtest (`strat_range_backtest.pine`)

Strategy version of the setups, run on the chart timeframe (15m / 30m / 60m). Entry = stop order at the
trigger, valid for the next candle only; stop = other side of the setup candle; target T1 / T2 / 1R / 2R;
flat at 16:00 ET. 100 shares, 1 tick slippage, no commission.

First results (2026-11-21, edge filter on, all patterns):

| Run | Trades | Win % | Profit factor | Long PF | Short PF |
|---|---|---|---|---|---|
| SPY 15m, T1 (Oct 2023 on) | 1,899 | 59% | 0.89 | 1.03 | 0.81 |
| SPY 60m, T1 (2015 on) | 2,004 | 59% | 0.90 | 0.94 | 0.88 |
| QQQ 30m, T1 (2020 on) | 2,027 | 61% | 0.90 | 0.99 | 0.83 |
| QQQ 60m, T1 (2015 on) | 1,812 | 57% | 0.88 | 0.98 | 0.81 |
| SPY 60m, long only, 2R | 386 | 46% | 1.06 | 1.06 | - |
| SPY 60m, long only, T2 | 385 | 45% | 0.98 | 0.98 | - |

Takeaways: T1 is too close (wins are ~0.6x the losses); shorts at the top lose; longs at the bottom with a
2R target are the only positive version, and only barely. Next: per-pattern breakdown, a daily-trend /
timeframe-continuity filter, and the same on QQQ.
