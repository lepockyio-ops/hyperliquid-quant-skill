# FreqAI frequency unlock and mode pruning

Use this reference when a repaired FreqAI strategy barely trades, the user wants materially higher activity, and the first relaxation pass increases count but destroys expectancy.

## Session pattern captured
- Baseline repaired variant (V4) was too tight: only 2 trades in ~89 days.
- User target was trader-style activity: average hold 1-5h and roughly 45-60 trades/month.
- A high-activity variant (V5) was created with:
  - BTC/ETH both reopened for long and short.
  - Pair/side quality gates relaxed (`delta`, `z-score`, `sigma`, `quantile`, `DI`).
  - Structure filters loosened.
  - Multiple entry modes encoded via `enter_tag`: `direct`, `pullback`, `trend`.
  - `label_period_candles` shortened to 4 to better match short holding intent.
  - `max_open_trades` raised to 4.
- Result: trade count jumped from 2 to 50, but expectancy collapsed.

## Why this matters
A near-zero-trade strategy and an overtrading strategy are two sides of the same funnel problem. The correct diagnosis is not just "tight" or "loose"; it is **which unlocked path produced the bad activity**.

## Durable workflow
1. Build the frequency-unlock candidate as a separate variant.
2. Tag entry modes explicitly (`direct`, `pullback`, `trend`).
3. Run the same backtest window with the same wallet assumptions.
4. Read `enter_tag` stats before touching thresholds again.
5. Prune the toxic mode first.
6. Only then revisit global threshold tightening/loosening.
7. Check average holding time against the target horizon; if it collapses below target, fix exit/horizon alignment next.

## Concrete evidence from this session
High-activity backtest showed:
- 50 total trades over ~61 active backtest days.
- Average duration only ~44 minutes, below the intended 1-5h hold profile.
- `freqai_short_direct_v5` produced 23 trades with effectively 0% win rate and dominated losses.
- `freqai_short_pullback_v5` had more tolerable but still weak behavior.
- Long-side activity increased, but aggregate expectancy remained clearly negative.

Interpretation:
- The trade-count unlock worked.
- The edge did not survive the unlock.
- The first rollback candidate is the toxic mode (`short_direct`), not a blanket re-tightening of every filter.
- The second repair target is hold-horizon mismatch: sub-hour average duration means the system is still behaving more like churn than 1-5h swing-through-intraday trading.

## Recommended next-step order
1. Disable the worst `enter_tag` bucket first (here: `short_direct`).
2. Re-test with all other settings unchanged.
3. If duration is still too short, adjust exit behavior / stop tightening / label horizon alignment before reopening more modes.
4. Only after mode pruning should you consider expanding the pair universe or adding more concurrent positions.

## Implementation cues
- Encode mode-specific tags directly in `populate_entry_trend` so backtest reports expose them natively.
- Route order-pricing logic through `custom_entry_price` keyed by `entry_tag`.
- Use separate pricing logic per mode:
  - `direct`: near current proposed price
  - `pullback`: ATR-based retracement limit
  - `trend`: ATR-based breakout-follow limit
- Keep these behaviors explicit rather than hidden behind one generic entry function, so PnL can be attributed by mode.
