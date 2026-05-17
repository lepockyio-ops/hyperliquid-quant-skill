# FreqAI / Hyperliquid strategy optimization playbook: execution A/B, edge audit, factor health

Use this when a Freqtrade + Hyperliquid + FreqAI strategy is losing money and the question is whether the main problem is execution structure, model edge, or dead external factors.

## Why this matters
A strategy can look broken for three very different reasons:
1. **Execution structure** is bad (retracement limit entries, same-candle stopouts, timeouts).
2. **Model edge** is too weak after fees / stop structure.
3. **External parquet factors** are wired in but effectively constant, so they add no signal and get stripped by feature selection.

This session produced a repeatable workflow that separates those three failure modes cleanly.

## Recommended order of operations
Follow this order unless the user explicitly asks otherwise:

### 1) Fix / test execution structure first
Do not start by tuning thresholds blindly.

Run controlled A/B backtests against the **same timerange**:
- Variant A: direct signal entry (`custom_entry_price = proposed_rate` and `adjust_order_price(..., is_entry=True) = proposed_rate`)
- Variant B: shallow retracement entry (e.g. 0.10 ATR)
- Optional baseline: previous deeper retracement (e.g. 0.25 ATR)

Important practice:
- Give each backtest variant a **separate `freqai.identifier`** so cached predictions/models do not contaminate comparisons.
- If you need a quick experiment, clone the strategy class into a sibling file rather than mutating the production file in place.
- Export trades and compute:
  - total PnL
  - entry timeouts
  - same-candle trade count (`trade_duration == 0`)
  - same-candle PnL
  - pair-level breakdown

Strong signal of execution-structure damage:
- many entry timeouts
- many same-candle stopouts
- same-candle losses dominate total losses

### 2) Test whether stoploss is the root cause or just changing the shape of bad trades
Create a loose-stop variant (for example, wider ATR multiple) and compare:
- total PnL
- same-candle count / PnL
- average loser duration
- exit reason mix

Interpretation:
- If wider stop lowers same-candle count **but worsens total PnL**, then stoploss is not the root cause.
- That usually means the entries are still low quality; the losses are merely delayed, not fixed.

### 3) Audit model edge directly from indicator dataframe
Do not rely only on backtest trade summaries.
Inspect the advised dataframe and calculate forward realized returns.

Useful metrics:
- `do_predict == 1` sample quality
- rows where prediction exceeds long threshold (`predicted > target_roi`)
- rows where prediction exceeds threshold plus explicit edge margin
- short-side equivalent (`predicted < sell_roi` / minus edge)
- future 24h realized return distribution
- median, mean, quartiles, winrate

Why this matters:
- A positive mean with weak median / winrate often means a few outsized winners are masking many low-quality signals.
- That is a warning sign for 1h execution with fees and stoplosses.

### 4) Audit parquet factor health before tuning feature-selection knobs
Check each external factor parquet for:
- row coverage in backtest window
- max/min timestamps
- null rate
- `nunique`
- standard deviation

Then inspect training logs for lines like:
- `VarianceThreshold will remove ...`
- `DI tossed ... predictions ...`

Interpretation:
- If factor columns have `nunique == 1` and `std == 0`, the issue is **dead source data**, not an over-aggressive `VarianceThreshold`.
- In that case, do **not** spend time adjusting feature selection first.
- Fix the upstream factor generation so the data actually varies.

## Verification tips
- Read the exported backtest zip / artifacts to verify which strategy code actually ran. This prevents confusion when the live strategy file changed after the backtest.
- Compare pair-level PnL. If one pair (often ETH) is the main drag while BTC is near flat, recommend temporary BTC-only runtime or stricter pair-specific thresholds.
- Watch for logs showing external factor features removed repeatedly by `VarianceThreshold`; that is a strong clue the parquet inputs are flat constants.

## Common conclusions to communicate clearly
- "Direct entry beats retracement entry" means the structure, not just the depth, is the problem.
- "Wider stop reduces same-candle losses but worsens total PnL" means stoploss was not the real root cause.
- "Model edge exists in averages but not in median/winrate" means the signal is too thin for current execution friction.
- "Factors are present but constant" means they are connected technically but useless statistically.

## Deliverable shape for the user
Summarize in trader-readable language:
1. execution verdict
2. stoploss verdict
3. model-edge verdict
4. factor-health verdict
5. immediate runtime recommendation (e.g. BTC-only, disable ETH, keep direct entry)

Keep the explanation concrete: same-candle losses, timeouts, pair drag, and whether the model truly has enough edge after execution friction.
