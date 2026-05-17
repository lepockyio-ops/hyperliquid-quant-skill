# FreqAI external factor integration checks

Use this when a Freqtrade + FreqAI strategy reads external parquet factors (for example Vibe funding / liquidation / onchain data) and you need to prove whether the ML model is actually using them.

## Core distinction

There are two separate paths:

1. **Strategy logic path**
   - Usually built in `populate_indicators()`.
   - Can drive hard entry gates, trend filters, and trader-visible columns.

2. **FreqAI feature-engineering path**
   - Built from `feature_engineering_expand_all()` / `feature_engineering_expand_basic()` / `feature_engineering_standard()`.
   - Only features created here become model-training inputs.

A factor can be fully active in live strategy gating while still being absent from the ML model.

## Reliable verification sequence

### 1) Check the strategy dataframe
Confirm the external factor columns are present after merge, for example:
- `funding_diff_hl_bn`
- `basis_bp`
- `liq_cluster_dist_pct`
- `whale_net_flow_24h_usd`

This proves the strategy path sees them, not that FreqAI trains on them.

### 2) Check engineered FreqAI feature names
Confirm the strategy creates `%`-prefixed model features in a `feature_engineering_*` hook, for example:
- `%-funding_diff_hl_bn`
- `%-basis_bp`
- `%-liq_cluster_dist_pct`
- `%-whale_net_flow_24h_usd`

If they are only created in `populate_indicators()`, the ML pipeline may never see them.

### 3) Retrain with a fresh identifier
If engineered features changed, rotate the `freqai.identifier` so FreqAI cannot reuse an incompatible cache/model family.

### 4) Inspect model metadata
The decisive check is the latest trained model metadata:
- `training_features_list` should include the expected `%` feature names.

Interpretation:
- **Missing from metadata** -> wiring failure: factors never entered the FreqAI training path.
- **Present in metadata** -> wiring succeeded.

### 5) Inspect feature-selection logs
If the features are present in metadata but do not survive to effective training, inspect logs for selectors like:
- `VarianceThreshold will remove N features`

Interpretation:
- If the newly added factors are the removed features, the merge is correct but the series is too flat / low-variance / uninformative in the chosen training window.
- The next step is data diagnosis, not pipeline rewiring.

## Practical diagnosis framing

When a user asks "did the factors get into FreqAI or not?", answer with three layers:

1. **Strategy layer**: are the raw external columns present?
2. **Training layer**: do engineered `%` features appear in `training_features_list`?
3. **Selection layer**: did filters keep or discard them?

This prevents a common false conclusion where "I see the columns in the dataframe" is mistaken for "the model trained on them".

## Typical next actions when selectors remove the factors

- Inspect the parquet series over the training window for near-constant values.
- Check whether resampling/forward-fill made the factor too flat.
- Reconsider whether the factor should stay as a hard gate instead of a model input.
- Only adjust feature-selection settings after confirming the factor is conceptually useful and not just noise.
