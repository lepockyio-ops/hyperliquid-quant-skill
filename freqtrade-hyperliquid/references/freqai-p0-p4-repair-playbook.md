# FreqAI P0–P4 repair playbook for Hyperliquid strategies

Session-derived workflow for diagnosing a Freqtrade + Hyperliquid + FreqAI strategy that looks directionally biased and suffers from unrealistic execution artifacts.

## When to use

Use this when a Hyperliquid FreqAI strategy shows one or more of:

- same-candle entry then immediate stopout / suspiciously fast stop losses
- strong long bias with zero or near-zero short trades
- factor gates that appear to be enabled but contribute no real variation
- LightGBM models that technically train but produce weak or one-sided predictions
- backtests where reducing size materially improves drawdown but not expectancy

## Durable lessons from this session

### 1) Check whether external factor parquet files are constant-valued

Do not assume Vibe / shared parquet factors contain usable information just because `_factors_available` is true.

In this session, the merged factors were effectively constants across the whole test window:

- `funding_diff_hl_bn = 0.0001`
- `basis_bp = 1.5`
- `liq_cluster_dist_pct = 0.8`
- `whale_net_flow_24h_usd = 1000000`

This created two hidden problems:

- they added almost no model information
- directional gates based on sign (`>= 0`, `<= 0`) became permanently one-sided

### 2) Diagnose signal bias by counting gate passes, not only completed trades

Before changing strategy logic, count for each pair:

- total candles
- `_factors_available`
- `do_predict == 1`
- edge passes for long / short
- trend passes for long / short
- final long / short entries

This separates:

- model bias (`short_edge` near zero)
- gate bias (e.g. whale/funding gate blocks one side)
- execution bias (entries exist but backtest deteriorates because of fill assumptions)

A strong example from this session: `whale_short` was always false because whale flow was a constant positive number, so the short side was structurally blocked even before trade quality was evaluated.

### 3) If a factor is flat, do not let it act as a directional veto

A robust fallback is:

- compute rolling std / variation flags for each external factor
- only enforce directional gate logic when the factor actually varies in-window
- otherwise treat the factor as unavailable for direction filtering rather than as a permanent long or short bias

Useful derived columns:

- rolling z-score
- rolling delta from rolling mean
- boolean `factor_var` flags from rolling std

### 4) Reduce execution optimism before judging alpha

To remove false edge from backtests, first make execution harsher / more realistic:

- replace direct entry at `proposed_rate` with ATR-based retracement pricing
- lower leverage caps
- lower stake caps
- keep the strategy variant separate from the production candidate

In this session, tightening execution realism and risk reduced drawdown materially, which is useful even though expectancy remained negative.

### 5) Recenter FreqAI targets when the label has a persistent directional drift

If the forward-return label is persistently positive or negative, try subtracting a rolling centerline from the raw forward return before assigning `&-s_close`.

This does not guarantee balanced long/short predictions, but it helps distinguish regime-relative edge from raw market drift.

## Practical repair sequence

1. Keep the original strategy untouched.
2. Create a `...Repaired` variant.
3. Add diagnostic scripts / tests for gate counts and factor variation.
4. Verify whether parquet factors vary in the tested window.
5. If factors are flat, add rolling transforms and variation-aware gating.
6. Add more realistic entry pricing (ATR retracement instead of direct fill).
7. Reduce leverage and stake caps before assessing true edge.
8. Recenter the target label if model predictions are strongly one-sided.
9. Re-run a short diagnostic script first, then a full 2–3 month backtest.
10. Compare repaired vs baseline on:
   - total profit
   - drawdown
   - average stake
   - long/short entry counts
   - stop-loss frequency and zero-duration losses

## Important interpretation rule

If repaired execution and smaller sizing improve drawdown but the strategy is still negative, treat that as a successful cleanup pass, not a trading success. It means the backtest is now closer to the real edge — which may simply be weak or absent.
