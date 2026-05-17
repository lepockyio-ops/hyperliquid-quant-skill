# FreqAI signal purification with mode preservation

Use this reference when a multi-mode FreqAI strategy is losing money, the blotter already shows per-mode attribution, and the user explicitly rejects deleting modes. The governing constraint is: keep `direct`, `pullback`, and `trend` present, then improve signal quality within each mode.

## Session pattern captured
- Baseline system: Hyperliquid + Freqtrade/FreqAI short-horizon stack.
- Entry modes already explicit via `enter_tag`: `direct`, `pullback`, `trend` on both long and short sides.
- Prior repair step (`mode pruning`) improved headline PnL by removing one catastrophic mode, but the user then corrected the workflow: do not keep solving this class of problem by deleting modes.
- Preferred sequence from the user: preserve structure, purify signals, re-backtest, then inspect whether edge is actually emerging.

## Durable repair levers that worked

### 1) Add candle-geometry gates
Useful first-line filters when backtests show too many noisy/impulsive entries:
- **Minimum body percentage**: require candle body to be a meaningful fraction of total range.
- **Range-vs-ATR cap**: reject signal candles whose full range is too large relative to recent ATR.
- **Close-location strength**: require the close to finish near the directionally favorable end of the bar.
- **EMA-distance / overextension cap**: keep breakout-style entries from chasing already stretched price.

These are especially useful when the failure mode is “signal fired on a dramatic candle that looked strong but immediately mean-reverted.”

### 2) Tighten FreqAI conviction asymmetrically
Do not treat long and short confidence thresholds as symmetric by default.
Useful levers:
- higher long quantile threshold
- lower short quantile threshold (i.e. require more extreme bearish prediction)
- stronger sigma / z-score requirements by side

In the captured case, raising conviction thresholds materially reduced low-quality activity without deleting any mode.

### 3) Preserve mode existence with tests
When the user rejects pruning, failing tests should guard two things at once:
1. each mode still exists conceptually (`direct` / `pullback` / `trend` paths remain reachable)
2. each mode now demands cleaner structure than before

This prevents a refactor from silently converting “signal purification” back into “implicit mode deletion.”

## What to measure after the purification pass
Do not look only at total PnL.
Read these three together:
- **total PnL / profit%**
- **same-candle stopout count and same-candle PnL**
- **average hold duration**

Interpretation pattern:
- If trade count falls, same-candle losses collapse, and average hold duration increases, the signal funnel is probably getting healthier.
- If PnL improves sharply but expectancy is still negative, call it what it is: **damage containment**, not proven edge.
- Then re-run `enter_tag` attribution to see which mode is still poisoning the result after purification.

## Concrete metrics pattern from this class of session
A successful purification pass can look like:
- materially fewer trades
- materially lower drawdown
- much lower same-candle loss concentration
- longer average holds
- still-negative expectancy overall, with one or two modes remaining toxic

That outcome means the next move is **further mode-specific purification**, not necessarily a return to blunt global threshold sweeps.

## Recommended next step after this pattern
Once purification improves the blotter:
1. keep all modes present
2. localize residual damage by `enter_tag`
3. apply side- and mode-specific quality tightening next (often short-side trend/pullback first)
4. only revisit model-family or target redesign after the purified signal funnel has been measured cleanly
