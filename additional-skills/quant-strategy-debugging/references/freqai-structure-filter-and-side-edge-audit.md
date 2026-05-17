# FreqAI structure-filter and side-edge audit

Use this pattern when a FreqAI strategy is still losing money after the pipeline is technically working.

## Situation
- Backtest is live enough to produce trades.
- Unit tests pass.
- Aggregate PnL is still negative.
- Trade blotter suggests same-candle stopouts, high-chase entries, or asymmetric long/short behavior.

## High-value diagnosis sequence

1. **Audit edge by side, not only aggregate prediction score**
   - Compare long predictions vs future returns separately from short predictions.
   - Bucket predictions by threshold (for example `mean + k*std` and `mean - k*std`) and inspect realized forward return by bucket.
   - Do not assume one symmetric regression target is equally useful for both directions.

2. **Inspect execution-path artifacts before tuning the model**
   - Count same-candle trades.
   - Compare same-candle PnL against all other trades.
   - If a large share of losses comes from instant fill-and-stop behavior, reduce those artifacts first.

3. **Add a structure filter before relaxing thresholds**
   Durable first-pass filters:
   - minimum candle body ratio (avoid weak/noise candles)
   - volatility cap relative to ATR (avoid panic expansion entries)
   - EMA deviation cap (avoid buying/selling too far from local trend anchor)

4. **Validate with paired before/after metrics**
   Track at least:
   - total profit %
   - trade count
   - same-candle trade count
   - same-candle PnL contribution
   - long vs short trade count / PnL

## Durable lessons

- A version that is still slightly negative can still be the right baseline if it removes obvious execution damage and sharply reduces overtrading.
- Structural filters are often more valuable than immediate label/threshold tweaking when the blotter shows bad physical trade quality.
- If long-side sample edge is negative while short-side threshold buckets show positive information, stop treating the model target as directionally symmetric.
- The next experiment after this diagnosis should usually be **asymmetric labeling or classification**, not blind hyperopt.

## Good follow-up experiments
- Long-only with structure filter preserved.
- Short-only variant using negative-score buckets.
- Classification target such as "will price move > X% before stop window expires?"
- Separate labels or thresholds for long and short paths.
