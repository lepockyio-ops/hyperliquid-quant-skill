# Unlimited leverage validation pattern for this Hyperliquid stack

## When this reference is useful
Use this when the user asks for a backtest that changes execution/risk envelope parameters without changing the main strategy permanently — especially `max_open_trades`, fee, pair set, wallet size, or `杠杆不限` / unlimited leverage.

## Durable pattern
1. Create a temporary validation subclass, e.g. `HLAdaptiveTrendFreqAI3mValidation`.
2. Override only the controls that would otherwise mask the experiment:
   - set `stake_cap_usd` very high
   - set `leverage_hard_cap` very high
   - override `custom_stake_amount()` to bypass subclass stake caps while still respecting `min_stake` / `max_stake`
   - override `leverage()` to return `max_leverage`
3. Clone the runtime config to a dedicated validation file and change only the requested scenario knobs:
   - `max_open_trades`
   - wallet size / fee / whitelist / FreqAI identifier
4. Run backtest with `--cache none` and a dedicated backtest output prefix.
5. Inspect the exported zip JSON, not just console summary.

## Why the exported zip matters
For Hyperliquid `unlimited leverage`, the effective answer is not "infinite leverage". The backtest will still use the exchange-reported `max_leverage` for each filled trade. Verify from:
- `trades[*].leverage`
- `trades[*].stake_amount`
- pair distribution
- long/short split
- `trade_duration`
- `exit_reason`

## Concrete finding from this session
In the 2026-02-01 to 2026-05-01 validation run:
- effective leverage on filled trades was **25x**
- BTC had **0 trades**
- ETH had **13 long trades**
- **all 13 trades had `trade_duration == 0`**
- losses came from `stop_loss` and `trailing_stop_loss`

This is a strong indicator that the first follow-up should be **execution-path A/B diagnosis**, not immediate feature tuning. In trader language: the strategy may be getting tagged out inside the same 1h bar it enters, so you may be measuring bar-simulation artifacts as much as signal quality.

## Recommended post-run questions
- Did unlimited leverage actually map to exchange max leverage, and what was the number?
- Were losses concentrated in one pair or one side only?
- Are most losses same-candle (`trade_duration == 0`)?
- Are stoploss/trailing-stop exits dominating before any multi-bar holding can occur?
- Did the external factors survive ML preprocessing, or were they removed by `VarianceThreshold` while still gating strategy entries?

## Suggested next-step framing
If the run shows all or most trades at zero duration, frame the next task as:
- "A/B isolate execution-path artifacts" rather than
- "retune the model"

Examples of A/B targets:
- capped vs uncapped leverage
- base execution vs simplified stoploss/exit path
- same strategy with/without trailing stop tightening
- direct-entry validation variant vs pullback / custom pricing variant
