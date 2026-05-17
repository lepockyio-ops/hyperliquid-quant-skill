---
name: quant-sample-window-and-data-fallbacks
description: Validate effective backtest sample windows for quant strategies, especially FreqAI/Freqtrade setups, and use cross-exchange fallback datasets when the primary venue lacks enough history.
---

# Quant sample window and data fallbacks

## When to use
- A quant/strategy session is drawing conclusions from backtests, trade counts, or monthly frequency.
- The stack uses Freqtrade, FreqAI, startup candles, informative timeframes, or long feature windows that can silently shrink the usable timerange.
- The primary exchange or venue has shallow historical data.
- The user wants 30/60/90-day validation but the local dataset may only support a few effective trading days.
- You need a pragmatic fallback dataset to validate signal density or entry-funnel losses before insisting on venue-pure data.

## Core principle
Never trust nominal timeranges alone. First compute the **effective sample window** after startup candles, model warmup, informative timeframe alignment, and available local history. Frequency conclusions drawn from a 3-7 day effective window should be treated as unstable.

## Workflow
1. **Audit actual local data coverage first.**
   - Confirm the earliest and latest timestamps for each pair/timeframe.
   - Compare that with the requested timerange.
   - Call out gaps explicitly.

2. **Estimate effective usable history, not raw history.**
   - Account for `startup_candle_count`.
   - Account for FreqAI label windows / feature windows / informative timeframe merges.
   - If the strategy uses 4h informative data or long rolling windows, note that the first usable trade can be much later than the first candle.

3. **Refuse to over-interpret short effective windows.**
   - If the user asks whether frequency hits a target like `30-60 trades/month`, do not extrapolate from a tiny usable sample without labeling it unstable.
   - State both raw timerange and effective timerange.

4. **Use a public fallback dataset when the primary venue is shallow.**
   - For crypto perpetual strategies, a practical fallback is a deeper Binance futures dataset for the same market regime.
   - Use the fallback to validate trade frequency, signal-funnel loss, and sensitivity studies.
   - Clearly label this as **cross-exchange validation**, not venue-identical execution proof.

5. **Keep the execution semantics separate from the research semantics.**
   - Use the deeper fallback dataset to answer: “Is the strategy structurally too restrictive?”
   - Use the primary venue dataset to answer: “Does this mapping still behave plausibly on the target venue?”

6. **Prefer evidence-driven iteration order.**
   - First fix execution structure (e.g. stoploss grace period, position handling).
   - Then verify sample sufficiency.
   - Then run A/B tests on labels, filters, and entry thresholds.
   - Only after that decide whether the model has edge or whether external factors dominate.

## Freqtrade / FreqAI specific checklist
- Check whether local history on the target exchange is actually deep enough for the requested timerange.
- Check whether `download-data` can fetch the target venue’s historical candles at the desired depth.
- If not, use preexisting local parquet or a fallback exchange dataset for research calibration.
- When using FreqAI, explicitly note:
  - `startup_candle_count`
  - label horizon
  - informative timeframe dependencies
  - any long rolling indicators/features
- When reporting results, include:
  - raw history span
  - effective trading span
  - trade count
  - implied monthly frequency only if the effective span is materially long enough

## Pitfalls
- **Pitfall: mistaking nominal timerange for usable timerange.**
  A 90-day backtest request can degrade into a 4-day usable window once warmup is applied.

- **Pitfall: concluding the strategy is too selective when the real issue is missing data depth.**
  Before loosening filters, verify the strategy had enough usable history to express itself.

- **Pitfall: treating Binance-fallback results as execution-identical to Hyperliquid.**
  Use fallback data for signal-density and robustness analysis, not as a perfect proxy for fill quality or venue microstructure.

- **Pitfall: changing many filters before fixing stop/hold mechanics.**
  If positions are getting washed out in minutes, solve that first or all frequency analysis will be distorted.

## Output style for future sessions
When presenting findings, separate them into:
1. **Data sufficiency** — what history was truly available.
2. **Effective sample** — what portion was actually tradable after warmup.
3. **What can be concluded now** — stable conclusions.
4. **What still needs longer data** — unresolved items.

## Support files
- `references/hyperliquid-binance-fallback.md` — concrete notes from a Hyperliquid/FreqAI case where shallow HL 5m history required Binance futures fallback for 90-day validation.
