# FreqAI pair-policy + zero-trade relaxation case

Use this reference when a repaired FreqAI strategy shows:
- very weak overall correlation
- one pair/side with obvious directional bias
- a first strict repair that collapses to zero trades

## Observed pattern
- BTC correlation was weak but less bad than ETH.
- ETH long predictions were biased optimistic; ETH short was the only remaining usable path.
- A first V4 pass with tighter conviction filters produced `0 trades` on main, BTC-only, and ETH-only backtests.

## Repair sequence that worked better
1. Add tests first for pair/side policy:
   - BTC long can still trigger under strong conviction.
   - ETH long is blocked.
   - ETH short remains enabled under strongly negative prediction.
2. Encode explicit pair policy instead of jumping straight to dual models:
   - `BTC/USDC:USDC`: allow long + short with milder thresholds.
   - `ETH/USDC:USDC`: `allow_long=False`, keep short only.
3. Keep conviction gates, but relax them in measured order after a zero-trade result:
   - lower `min_long_delta` / `min_short_delta`
   - lower `min_long_zscore` / `min_short_zscore`
   - reduce ATR extension requirements
   - widen signal-range tolerance
4. Reduce feature dimensionality at the same time:
   - `include_corr_pairlist = []`
   - `include_shifted_candles = 1`
   - `indicator_periods_candles = [14, 50]`
   - `train_period_days = 90`
5. Re-run:
   - full backtest
   - BTC-only
   - ETH-only

## Result shape
- Full V4 recovered from `0 trades` to a small number of trades.
- Trades came only from ETH short, which matched the diagnostic thesis.
- BTC remained at `0 trades`, which means the next step is BTC-specific gate relaxation or a dedicated BTC edge audit — not re-enabling ETH long.

## Lessons
- `0 trades` after tightening is evidence the signal funnel is overconstrained, not evidence the repair worked.
- If one pair/side is structurally bad, encode that asymmetry explicitly before paying the complexity cost of separate models.
- Feature reduction plus pair/side policy is often a better next move than immediate dual-model redesign.
