# FreqAI signal funnel analysis for low-trade / pair-biased strategies

Use this reference when a strategy is supposed to be short-horizon but backtests show very few trades, strong pair bias, or one-sided long/short behavior.

## Diagnostic pattern that worked

Run the analysis as a funnel, not as blind parameter tweaking:

1. **Prediction layer**
   - Count rows with `do_predict == 1` per pair.
   - Count `prediction > long_threshold` and `prediction < short_threshold` separately.
   - Report mean prediction and mean threshold by pair and side.
   - This quickly reveals whether the model is producing actionable edge at all, and whether the thresholds are effectively impossible for some pairs.

2. **Trend layer**
   - Starting from prediction-qualified rows, measure how many bars survive trend filters.
   - This distinguishes `model has no edge` from `model has edge but trend gate kills it`.

3. **Structure layer**
   - Measure incremental drop after candle-shape / extension / ATR-range filters.
   - In the session that produced this note, ETH long candidates fell from 251 prediction hits to 116 after trend, then to 18 after structure. The structure gate was a major mechanical reducer.

4. **Factor layer**
   - Check Vibe / external factors last, and report explicit block reasons.
   - If factors use SLA-based freshness gating, stale data can silently explain near-zero trade counts. In the session that produced this note, `onchain` was stale by ~118800 seconds and blocked entries.

5. **Episode grouping**
   - Group surviving candidate bars into contiguous signal episodes.
   - If candidate bars cluster in one short market regime, low trade count may be regime concentration rather than execution failure.

## Concrete findings worth reusing

### Horizon mismatch check
If actual average holding time is 1–5 hours but FreqAI uses `label_period_candles=24` on a 1h timeframe, the model is learning roughly a 24h target while execution is closing on a much shorter horizon.

This mismatch can produce all of the following at once:
- very low signal frequency
- thresholds that are too hard for some pairs
- entries that are exited before the learned edge has time to play out

**First A/B to run:** keep execution logic fixed and compare `label_period_candles` values like `24 vs 6 vs 4`.

### Pair bias check
If one pair has almost no threshold hits while another pair produces many, do not jump straight to strategy-wide threshold loosening. First isolate:
- BTC-only
- ETH-only
- long-only
- short-only

This tells you whether the issue is:
- no model edge for a specific pair
- asymmetric long/short learning
- overly strict shared thresholds

### Threshold design lesson
A shared threshold rule like `mean ± k*std + fixed_edge` can accidentally suppress one pair almost completely.

Prefer A/B tests on:
- pair-specific thresholds, or
- quantile-based thresholds (e.g. top/bottom x% of predictions)

Use this before relaxing structural filters. Otherwise you may only increase the number of bad trades.

### Ordering lesson
When results are poor, do **not** start by loosening candle/ATR structure filters just to increase trade count.

Use this order instead:
1. Fix prediction horizon / label alignment.
2. Recalibrate thresholds by pair and side.
3. Evaluate whether long and short should be learned separately.
4. Only then do controlled A/B tests on structural filter loosening.

This prevents turning `10 losing trades` into `30 losing trades`.

## Good metrics to report back to the user

For each pair and side, include:
- rows with `do_predict == 1`
- threshold-hit count
- threshold-hit rate
- mean prediction
- mean long/short threshold
- surviving bars after trend
- surviving bars after structure
- surviving bars after factor gate
- number of contiguous trade episodes
- actual executed trade count
- average holding time

## Minimal recommendation template

When the funnel points to a prediction-layer issue, recommend in this order:
- adjust `label_period_candles` to match real holding horizon
- recalibrate thresholds by pair / side
- run isolated pair-only and side-only backtests
- keep structure filters mostly intact until model alignment is fixed

## Session-specific evidence snapshot

- Backtest window produced only 10 trades, all ETH.
- Average holding time was about 1h48m.
- BTC predictions almost never crossed thresholds.
- ETH produced long-side prediction hits but no short-side hits.
- Structure filters reduced ETH candidate bars sharply.
- Stale onchain factor data was also present and could hard-block entries.

This pattern is a strong indicator that the root problem is upstream in label/threshold alignment, with factor freshness and structure filters acting as secondary reducers.
