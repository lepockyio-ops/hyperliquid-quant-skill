# FreqAI enablement notes for Hyperliquid + Freqtrade

## Trigger
Use when moving a working Hyperliquid/Freqtrade pipeline toward ML-assisted trading with FreqAI.

## Minimum config block
A usable `freqai` config needs at least:
- `enabled`
- `train_period_days`
- `backtest_period_days`
- `identifier`
- `feature_parameters`
- `data_split_parameters`

A practical starter shape:
- `train_period_days: 60`
- `backtest_period_days: 7`
- `identifier: hl-freqai-v1`
- `include_timeframes: ["1h", "4h"]`
- `include_corr_pairlist: ["BTC/USDC:USDC", "ETH/USDC:USDC"]`
- `label_period_candles: 24`
- `include_shifted_candles: 2`
- `indicator_periods_candles: [14, 20, 50]`
- `data_split_parameters: {test_size: 0.25, shuffle: false, random_state: 1}`

## Strategy requirements
A FreqAI strategy must add:
- `self.freqai.start(dataframe, metadata, self)` inside `populate_indicators()`
- `feature_engineering_expand_all()`
- `feature_engineering_expand_basic()`
- `feature_engineering_standard()`
- `set_freqai_targets()`

Conventions:
- `%` prefixed columns are features.
- `&` prefixed columns are targets / labels.

## Rollout advice
1. Keep the existing rule-based Hyperliquid strategy intact.
2. Create a parallel FreqAI strategy file for experiments.
3. Start with `LightGBMRegressor` and a simple forward-return target.
4. Use OHLCV + standard technical indicators first.
5. Keep Vibe funding/liquidation/onchain factors outside the model as hard gates until the ML pipeline is proven.
6. Only after the ML path backtests and dry-runs cleanly, experiment with moving external factors into the feature set.

## Verification
- `freqtrade list-freqaimodels` works.
- Strategy loads with `--freqaimodel LightGBMRegressor`.
- If running via Docker, use a FreqAI-capable image such as `freqtradeorg/freqtrade:stable_freqai`.
- Backtest timerange includes enough pre-history for `train_period_days` plus startup candles.
- `user_data/models/<identifier>/` artifacts appear after training/backtesting.
- On first live/dry-run boot, tolerate temporary `No model ready for <pair>` warnings if the bot stays in `RUNNING` and begins training.

## Cache / identifier reset rule
When FreqAI throws a feature-pipeline mismatch such as `Pipeline expected ... but got ...`, suspect stale cached models or historic predictions under the current identifier.

Use this recovery sequence:
1. Confirm the strategy's feature engineering really changed (new columns, reordered engineered features, changed pair set / timeframes, etc.).
2. Bump `freqai.identifier` to a new value (for example `hl-freqai-v2`).
3. Re-render the runtime config.
4. Restart the bot so FreqAI trains from a clean identifier namespace.

Avoid deleting caches blindly before you verify the identifier story; the durable lesson is that materially changed features should get a new identifier.

## Common mistakes
- Leaving a half-filled `freqai` block in config and assuming `enabled: false` is harmless.
- Reusing the exact same `identifier` after materially changing features or targets.
- Feeding only current-time factor rows into a historical FreqAI backtest.
- Judging FreqAI quality before the non-ML pipeline itself is stable.
