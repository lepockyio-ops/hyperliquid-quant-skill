# Zero-trade diagnosis for factor-gated Freqtrade / FreqAI backtests

## Symptom

Backtest completes successfully, but reports `0 trades` even though:
- OHLCV parquet loads correctly
- strategy imports correctly
- informative timeframe data exists
- FreqAI produces prediction columns (`do_predict`, `&-s_close`, etc.)

## High-probability root cause

A factor-gated strategy uses a freshness / SLA check based on wall-clock time, e.g.:

```python
now = pd.Timestamp.utcnow().tz_localize(None)
latest_age = now - pd.to_datetime(tbl["as_of"].iloc[-1])
if latest_age > max_age:
    df["_factors_available"] = False
```

In historical backtests this is wrong: the strategy is evaluating old factor rows against *today* rather than against the historical candle being backtested.

Result: `_factors_available` becomes false for every row, so entries are silently blocked.

## Minimal diagnostic procedure

Instrument the analyzed dataframe and count rows for:
- `_factors_available`
- `_factors_block_reason`
- `do_predict`
- prediction > entry threshold (`&-s_close > target_roi` or equivalent)
- prediction < exit/short threshold
- trend filters (`close > ema_4h_4h`, EMA crosses, ADX filters)
- final long / short candidate counts

Interpretation order:
1. If `_factors_available` is false for nearly all rows, fix gating before touching ML thresholds.
2. If factors are available but `do_predict` is mostly zero, inspect FreqAI feature/label readiness.
3. If factors and predictions exist but threshold comparisons rarely pass, then tune thresholds or target construction.

## Smallest safe fix

For historical backtests, evaluate freshness against candle context instead of wall-clock time. Safe options:
- compare factor `as_of` to the dataframe/candle `date`
- rely on `merge_asof(..., tolerance=max_age)` and then only check for missing merged fields
- branch behavior so live/dry-run uses wall-clock freshness while backtests use candle-time freshness

The fix should preserve the hard gate; only the time reference changes.

## What not to do first

- Do **not** loosen entry thresholds before proving factor gating is not the blocker.
- Do **not** remove factor gating entirely if the real issue is stale logic tied to wall-clock time.
- Do **not** assume `0 trades` means FreqAI failed; prediction columns may be present while gates block all entries.

## Typical evidence pattern

A representative blocked run looked like:
- `_factors_available = False` for all rows
- `_factors_block_reason = stale:onchain:<seconds>`
- `do_predict = 1` on almost all rows
- some threshold passes existed, but final entry counts remained zero because factor gating happened earlier

Use this pattern to separate pipeline failures from strategy-logic failures.
