# FreqAI PnL improvement with mode pruning (V5 -> V5.1)

## When this reference matters
Use this when a FreqAI strategy has already been expanded to hit a higher monthly trade count, but aggregate PnL is badly negative and you need to improve results **without changing the overall framework**.

## Baseline situation
A 1h Hyperliquid FreqAI strategy had already been widened into a high-activity V5 variant:
- same FreqAI + LightGBMRegressor architecture
- same BTC/ETH focus
- same `direct` / `pullback` / `trend` mode structure
- target user preference: keep the framework, improve PnL

Observed V5 backtest outcome over the same 2026-02-01 to 2026-05-01 test window:
- 50 trades
- -7.66% total profit
- 44.4 min average hold
- 6.0% win rate
- `freqai_short_direct_v5` was the biggest poison: 23 trades, all losing, about -38.68 USDC

## High-leverage repair applied
Instead of redesigning the model or changing the overall architecture, the repair was:
1. add failing tests for the intended mode behavior
2. create a V5.1 variant that keeps the same framework
3. explicitly remove `short_direct`
4. tighten pair/side thresholds and structure filters around the remaining modes
5. re-run the exact same backtest window

## Concrete V5.1 mechanics
- keep timeframe = `1h`
- keep FreqAI with `LightGBMRegressor`
- keep BTC/ETH pair universe for the validated comparison
- keep multi-mode framework, but:
  - disable `short_direct`
  - require stronger short conviction for remaining short entries
  - slightly tighten long conviction as well
  - keep tests asserting that high-conviction `long_direct` can still fire

## Outcome
V5.1 improved the damage metrics, but did not create real edge:
- trades: 50 -> 31
- total profit: -7.66% -> -5.76%
- average hold: 44.4 min -> 23.2 min
- max drawdown: 7.68% -> 5.76%
- win rate: 6.0% -> 3.2%

Mode-level attribution after pruning:
- worst residual mode became `freqai_short_trend_v5_1`
- `freqai_short_pullback_v5_1` and `freqai_long_pullback_v5_1` were also negative
- only `freqai_long_trend_v5_1` showed even modest survivability

## Interpretation
This is a classic **loss reduction without edge creation** result.

What it means:
- removing the most toxic mode was still correct
- the framework is less bad than before
- but the strategy is not yet profitable
- average hold getting even shorter is a warning that entries are still being invalidated quickly, not that exits are smart

## Durable lesson
When a first PnL-improvement pass only makes the system lose money more slowly:
1. do not claim success just because drawdown improved
2. do not immediately reopen all modes with softer thresholds
3. use `enter_tag` attribution to find the next toxic branch
4. usually the next step is **mode concentration**, e.g. keep only the least-bad `long_trend` path and cut weak short-side branches before any global retune

## Test pattern worth reusing
Useful tests in this session class:
- short entries must not emit `short_direct`
- strong short-trend structure can still produce `short_trend`
- strong high-conviction long breakout can still produce `long_direct`
- config test asserts the framework stays the same while the identifier changes for a clean experiment

## Reporting pattern
For these sessions, always report both:
- **damage improvement**: PnL, drawdown, total loss
- **remaining edge status**: win rate, average hold, surviving mode profitability

If those two are mixed together, it is easy to mistake a cleaner failure for a working strategy.
