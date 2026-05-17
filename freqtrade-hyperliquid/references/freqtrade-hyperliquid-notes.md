# Freqtrade + Hyperliquid session notes

## Tested durable patterns

### Runtime config rendering
- Template config contained `${VAR}` placeholders.
- Runtime config must be materialized before running Freqtrade commands.
- Safe pattern:
  - template: `user_data/config.json`
  - rendered: `user_data/config.runtime.json`
  - permissions: `0600`
- Auto-enable policy:
  - `webhook.enabled = true` iff `DISCORD_WEBHOOK_URL` is non-empty
  - `telegram.enabled = true` iff both `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` are non-empty

### Freqtrade futures parquet layout
Source raw files looked like:
- `BTC_USDC_USDC__1h.parquet`
- `ETH_USDC_USDC__4h.parquet`

Freqtrade-readable destination files should look like:
- `user_data/data/hyperliquid/futures/BTC_USDC_USDC-1h-futures.parquet`
- `user_data/data/hyperliquid/futures/ETH_USDC_USDC-4h-futures.parquet`

Normalized OHLCV columns:
- `date`
- `open`
- `high`
- `low`
- `close`
- `volume`

Normalization steps that worked:
1. Parse `date` or derive from `timestamp`.
2. Convert to UTC.
3. Floor to millisecond precision.
4. Sort by `date`.
5. Drop duplicate timestamps.
6. Store via `ParquetDataHandler.ohlcv_store(..., CandleType.FUTURES)`.

### Validation commands that proved useful
```bash
freqtrade list-data \
  --config user_data/config.runtime.json \
  -d user_data/data/hyperliquid \
  --data-format-ohlcv parquet \
  --show-timerange
```

```bash
freqtrade backtesting \
  --config user_data/config.runtime.json \
  --userdir user_data \
  --strategy HLAdaptiveTrend \
  -d user_data/data/hyperliquid \
  --data-format-ohlcv parquet \
  --timerange 20251018-20260515 \
  --enable-protections \
  --export trades \
  --backtest-directory user_data/backtest_results
```

## Factor-gated strategy lesson
`HLAdaptiveTrend` requires external factor parquet and uses hard gating. A backtest can complete successfully with **0 trades** if:
- factor history does not span the backtest period, or
- the entry conditions never trigger.

For pipeline validation, a practical approach was to generate mock historical factor rows aligned to the 1h candle dates for every pair.

## Backtest result interpretation
Observed successful pipeline state:
- strategy loaded
- informative 4h data loaded
- protections loaded
- result zip/meta written
- total trades = 0

Interpretation:
- pipeline and storage were healthy
- remaining issue moved from infrastructure into signal generation / factor realism

## FreqAI note
Keeping an incomplete `freqai` block in config is risky even when conceptually disabled, because schema validation may still require fields such as model identifier and training windows. For a v7.1 "get it running" workflow, removing the block entirely is safer than leaving a partial stub.
