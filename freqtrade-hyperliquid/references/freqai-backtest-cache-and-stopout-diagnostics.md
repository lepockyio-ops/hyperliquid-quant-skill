# FreqAI backtest cache isolation and same-candle stopout diagnosis

## When to use

Use this note when a Freqtrade + Hyperliquid + FreqAI backtest behaves strangely after feature/schema changes, especially if you see either of these patterns:

- backtesting logs say it found existing historic predictions / trained timestamps, then crash with missing `*_metadata.json`
- results are deeply negative and a suspicious share of trades have `trade_duration == 0`

## Durable lessons

### 1) Analysis reruns should use a fresh identifier

If you changed FreqAI features, targets, or strategy behavior and want a clean diagnostic rerun, do **not** reuse the live `freqai.identifier` blindly.

Observed failure pattern:

- backtesting reuses `models/<identifier>/backtesting_predictions/...`
- queue reconstruction assumes a matching `sub-train-*` tree exists
- a partial or inconsistent model directory then causes `FileNotFoundError` on `*_metadata.json`

Safer pattern:

1. Copy the runtime config to a temporary analysis config.
2. Set `freqai.identifier` to a fresh value such as `hl-freqai-v4-analysis`.
3. Disable delivery/integration side effects in the temp config (`webhook`, `telegram`, `api_server`) so the run is obviously analysis-only.
4. Rerun backtesting against the fresh identifier.

Interpretation rule:
- if the clean rerun works, treat the old failure as a cache/artifact isolation problem, not evidence that the strategy code is still broken.

### 2) Same-candle losses often point to execution artifacts

In this session, poor results were dominated by trades that opened and closed in the same 1h candle. That is a diagnostic smell, not just a bad score.

Why it happens:

- `custom_entry_price()` places a pullback limit order
- backtest OHLC path allows that limit to fill intrabar
- custom stoploss / custom exit logic can also trigger intrabar
- result: entry and exit both occur inside the same candle, often at a loss

What to measure:

- share of trades with `trade_duration == 0`
- PnL contribution from same-candle trades versus all other trades
- pair split (often one pair, e.g. ETH, contributes most of the damage)
- side split (if almost all trades are long, the strategy is effectively one-sided)
- exit reason split (`trailing_stop_loss`, `stop_loss`, etc.)

Interpretation rule:
- if same-candle trades dominate losses, test the execution layer first before reworking the ML feature set.

### 3) External factors can be present but still useless

A factor appearing in `training_features_list` proves the wiring is fixed.
It does **not** prove the model trained on it.

Look for logs such as:

- `VarianceThreshold will remove ...`
- `Training model on N features`

If the newly added factors are named in the removal list, the next debugging step is factor variance / information content, not more merge-path patching.

## Recommended analysis sequence

1. Run a clean backtest with a fresh analysis identifier.
2. Capture summary metrics, pair split, side split, exit reasons.
3. Inspect trade-duration distribution.
4. Quantify same-candle loss contribution.
5. Inspect training logs for feature-pruning messages.
6. Only then decide whether the next A/B test should target:
   - entry execution (`custom_entry_price` / timeout behavior)
   - stop logic (`custom_stoploss` / exit pricing)
   - factor quality / variance
   - target definition / prediction thresholds
