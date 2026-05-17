---
name: quant-strategy-debugging
description: Diagnose and improve automated trading strategies by converting backtest failure patterns into targeted tests, minimal code changes, and verified before/after metrics.
---

# Quant Strategy Debugging

Use this skill when a trading strategy is losing money, overtrading, producing suspicious fills, or behaving differently than intended in backtests/dry-run. This is for **strategy triage and loss reduction**, not greenfield strategy design.

## When to use
- A Freqtrade or similar strategy is clearly underperforming and you need to find the highest-leverage fix.
- Backtests show pathologies like many same-candle stopouts, excessive entry timeouts, low-quality fills, or one asset dominating losses.
- You need to turn qualitative complaints ("entries are bad", "it keeps getting wicked out") into **tests + code changes + measured deltas**.
- The goal is first to **reduce bad losses and obvious execution mistakes**, even before proving positive expectancy.

## Core principle
Do not start by spraying parameter tweaks. First identify the failure mode that is most obviously destroying PnL, then make the **smallest test-backed code change** that should reduce that specific damage.

## Workflow

1. **Establish a baseline from the current strategy**
   - Capture total PnL, win rate, trade count, max drawdown.
   - Break out diagnostics that reveal structural failure modes:
     - same-candle trades / same-candle PnL
     - entry timeout count
     - long vs short counts and PnL
     - per-pair PnL
     - common exit reasons / stoploss concentration
   - Write these down before changing code.

2. **Translate observed pain into explicit hypotheses**
   Examples:
   - Many same-candle losers → entry execution is too passive / too optimistic.
   - Huge timeout count → custom entry pricing is preventing fills.
   - One pair dominates losses → asset-specific mismatch; test pair filtering before redesigning everything.
   - Short side never wins → disable or tighten shorts before touching longs.

3. **Add or update tests before changing behavior**
   Focus tests on intended strategy mechanics, not only helper functions.
   Typical durable tests:
   - entry-pricing test: strategy does / does not offset from proposed_rate
   - order-adjustment test: entry orders are / are not re-anchored after submission
   - entry-trend test: signal requires prediction edge plus trend alignment
   - factor-gating test: stale/missing auxiliary factors block entry
   - retrain / model-contract test: keep data/feature assumptions explicit

4. **Prefer minimal code changes that directly hit the pathology**
   High-leverage examples:
   - Remove pullback-limit entry logic if it creates timeouts and same-candle stopouts.
   - Tighten entry filters using both model edge and market structure, instead of prediction threshold alone.
   - Add simple structure filters when the blotter shows obvious bad trade geometry: minimum candle-body ratio, ATR/volatility cap, and EMA-deviation cap to avoid weak candles and overextended chase entries.
   - Keep execution changes and signal-quality changes conceptually separate so you can attribute improvement.

5. **Re-run targeted tests first, then the same backtest window**
   Use the exact same timerange / wallet / market set for before-vs-after comparability.
   Report:
   - absolute PnL delta
   - profit% delta
   - trade count delta
   - win-rate delta
   - same-candle PnL delta
   - timeout delta
   - pair-level delta

6. **Localize residual failure after the first fix**
   Once the biggest failure mode improves, identify the new bottleneck:
   - still losing overall but less → edge still weak
   - one asset still responsible for most damage → isolate to BTC-only / ETH-only / per-pair subsets
   - only one side trades now → short logic overconstrained or no usable short edge

7. **Use pair-isolation and side-isolation experiments before deeper rewrites**
   Before redesigning the whole strategy, run controlled variants like:
   - BTC-only
   - ETH-only
   - longs only
   - shorts only
   - structure-filtered vs unfiltered
   These often reveal that a strategy is not universally broken; one venue/asset/side or one execution pattern is poisoning the aggregate result.

8. **Audit model edge separately from execution quality**
   If the strategy uses FreqAI or another predictive model, inspect whether the model has usable information by side and by score bucket before changing labels or thresholds.
   - Compare long predictions against realized forward returns separately from short predictions.
   - Inspect threshold buckets (`mean ± k*std` or quantiles), not only overall correlation.
   - If long buckets are negative-edge while short buckets are informative, treat that as a model-target design problem rather than a generic "tune the parameters" task.
   - If execution artifacts (same-candle fill-and-stop, chasing extended candles) dominate losses, fix those first so the edge audit is not contaminated.

9. **State the conclusion honestly**
   Improvements that reduce losses are valuable, but do not overclaim. If the strategy is still negative expectancy, say so clearly. Distinguish:
   - execution pathology fixed
   - signal quality improved
   - production readiness not yet achieved

## Quant-specific pitfalls
- Do **not** confuse fewer trades with improvement unless PnL quality also improved.
- Do **not** accept aggregate results only; always inspect per-pair and per-side contributions.
- Do **not** keep passive pullback pricing just because it sounds prudent; if it causes missed fills or worst-case fills, remove it.
- Do **not** chase hyperopt before eliminating obvious mechanical errors.
- Do **not** declare success because drawdown improved if expectancy is still clearly negative.
- Do **not** assume a single symmetric regression label is equally valid for both long and short decisions. Side-specific edge can diverge sharply.
- Do **not** blame the model first when bad trades are visibly extended-candle chases. Add or test basic structure filters before redesigning the ML target.
- For **FreqAI** backtests, verify the model class before treating a run failure as strategy logic. Some configs enable `freqai` but omit the CLI model selection; in that case backtesting must pass `--freqaimodel <ModelClass>` explicitly (for example `LightGBMRegressor`) or the run aborts before any strategy diagnosis is valid.

## FreqAI horizon-repair sequence
When a FreqAI strategy is meant to hold for 1-5 hours but barely trades, or trades almost entirely one pair/one side, use this order of operations before redesigning the whole system:

1. **Fix horizon mismatch first**
   - Compare `label_period_candles` to real holding time.
   - If the model learns 24h forward returns while the strategy exits in ~1-5h, shorten the label horizon first (for example test 24 vs 6 vs 4) before blaming pair selection or structure filters.

2. **Then recalibrate thresholds by pair and side**
   - Do not assume one global `predicted > target_roi + edge` rule is appropriate for BTC and ETH equally.
   - Add pair-specific and side-specific threshold logic or rolling quantile gates before broad structural rewrites.
   - A useful test is whether the "dead" pair starts producing signals after threshold calibration without making the dominant pair explode in low-quality entries.

3. **Then isolate pair and side before deeper ML changes**
   - Run `BTC-only`, `ETH-only`, `long-only`, and `short-only` backtests.
   - This tells you whether the strategy has one poisonous pair or one poisonous side, instead of only showing an aggregate red number.
   - Treat these isolation runs as decision tools, not permanent production settings.

4. **Before splitting models, try pair/side policy with explicit tests**
   - If one pair/side is clearly biased (for example ETH-long keeps fabricating optimism while ETH-short is the only usable edge), encode that as explicit policy first: `allow_long=False` for the bad side, side-specific thresholds for the remaining path, and tests that assert the behavior.
   - Write failing tests for representative cases such as: BTC long still allowed under strong conviction, ETH long blocked even under superficially positive predictions, and ETH short still allowed when prediction is strongly negative.
   - This gives you a cheaper, more falsifiable step before paying the complexity cost of dual-model or dual-label architecture.

5. **Then decide whether to split targets/models**
   - If long and short remain materially different after horizon + threshold repair plus pair/side policy, move to side-specific targets or separate long/short models.
   - Do not jump straight to dual models if simpler isolation already shows that one pair is the real problem.

6. **Treat zero-trade outcomes as overconstraint, not success**
   - After tightening conviction gates, immediately run the same backtest window and also `BTC-only` / `ETH-only` variants.
   - If the result is `0 trades`, do not celebrate the absence of bad trades. It usually means the signal funnel is too tight.
   - Relax in a controlled order: prediction edge threshold, z-score requirement, ATR extension requirement, then candle-geometry filters. Re-test after each step.
   - Preserve the directional lesson while relaxing: do **not** re-enable the known-bad side just to recover activity.

7. **Use dimensionality reduction as a first-class repair when feature/sample ratio is unhealthy**
   - If FreqAI diagnostics show many more features than effective samples, shrink the feature set before redesigning the model family.
   - Practical levers that worked well in this session class:
     - remove `include_corr_pairlist` entirely when cross-asset features look like noise transfer rather than edge
     - reduce `include_shifted_candles` (for example `2 -> 1`)
     - compress indicator periods to a small core set (for example `[14, 50]`)
     - extend `train_period_days` enough to improve sample density (for example `60 -> 90`) while keeping the same evaluation window
   - Verify the result in training logs: look for actual feature count and training row count, not just config intent.

8. **Be conservative about structure-filter loosening**
   - If loosening structure filters raises trade count but worsens expectancy sharply, interpret that as evidence that the model edge is still weak.
   - In that case, roll back some loosened filters and continue from the best isolated subset (often the less-bad pair) instead of keeping the broader configuration.

9. **When targeting a higher monthly trade count, separate `frequency unlock` from `edge validation`**
   - For 1h FreqAI systems that need to move from near-zero trades toward a target like ~45-60 trades/month, first create an explicit high-activity variant rather than silently mutating the production candidate.
   - The useful levers are: shorten `label_period_candles` toward the real hold horizon, relax pair/side `delta` and `z-score` gates, loosen structure filters modestly, and raise `max_open_trades` so concurrency is not the hidden limiter.
   - Add explicit entry-mode tags such as `direct`, `pullback`, and `trend` so the blotter can tell you which activity source is actually working.
   - After the first frequency-unlock backtest, read PnL by `enter_tag` before doing more tuning. If one mode explodes in count but has near-zero win rate or dominates losses, prune that mode first instead of globally tightening everything again.
   - Also compare realized holding time against the intended 1-5h horizon. If frequency rises but average duration collapses to sub-hour churn, treat that as a horizon/exit mismatch rather than a success.

This sequence matches the user's preferred workflow for quant strategy iteration: fix execution/structure mismatches first, then run A/B backtests, then judge model edge, and only after that revisit external-factor explanations.

Reference: `references/freqai-frequency-unlock-and-mode-pruning.md` — concrete case of moving from 2 trades to 50 trades with direct/pullback/trend entry modes, then using enter-tag stats to identify the toxic mode and the hold-horizon mismatch.

10. **After pruning the worst mode, compare `loss reduction` separately from `edge creation`**
   - A useful intermediate step is a `PnL-improvement` variant that keeps the same framework (same model family, timeframe, pair universe, and overall signal funnel) but removes the most toxic mode and tightens only the directly related gates.
   - Judge this variant on two separate questions:
     1. Did total loss and drawdown improve?
     2. Did the strategy actually gain positive expectancy, or merely lose money more slowly?
   - If PnL improves but average holding time collapses further or win rate remains near zero, interpret that as **damage containment**, not proof of edge.
   - In that case the next move is usually **mode/side concentration** (for example, keep only the least-bad `long_trend` path) rather than another global threshold sweep.

11. **Use enter-tag PnL as the primary attribution lens for multi-mode FreqAI variants**
   - When a strategy uses tags such as `direct`, `pullback`, and `trend`, always extract PnL by `enter_tag` from the exported backtest result before deciding what to tighten.
   - If one tag is catastrophic (for example a `short_direct` breakdown-chase mode with high count and zero wins), identify it explicitly before changing the whole funnel.
   - Do not summarize only aggregate strategy metrics; multi-mode systems need **mode-level attribution** or you will keep tuning the wrong component.

12. **If the user explicitly wants all modes preserved, pivot from pruning to signal purification**
   - Treat “do not cut modes” as a workflow constraint, not a suggestion. The next iteration should keep `direct` / `pullback` / `trend` alive and make each one earn entry through stricter quality gates.
   - The durable first-line levers are:
     - candle-geometry filters (minimum body percentage, close-location strength, cap on range-vs-ATR)
     - side-specific FreqAI conviction tightening (higher long quantiles, lower short quantiles, stronger sigma / z-score requirements)
     - extension guards (EMA-distance / overextension caps) so breakout modes stop chasing already-exhausted bars
   - Write failing tests that prove each mode still exists **and** now requires cleaner structure. This prevents accidental silent pruning during a refactor.
   - Judge the result on three axes together: total PnL, same-candle stopout concentration, and average hold duration. A good purification pass often reduces trade count while materially cutting same-candle losses and lengthening average holds.
   - After the purification pass, re-read `enter_tag` attribution again. The goal is to discover which mode remains toxic **after** quality tightening, not to jump straight back to global threshold tuning.

Reference: `references/freqai-pnl-improvement-with-mode-pruning.md` — concrete V5→V5.1 case where removing `short_direct` improved PnL and drawdown but did not create real edge, revealing that the next leverage point was concentrating away from weak short-side modes rather than broad retuning.

Reference: `references/freqai-signal-purification-with-mode-preservation.md` — concrete V5.2 case where the user rejected mode pruning, so the repair kept all entry modes and instead improved PnL via geometry filters, stronger FreqAI conviction gates, and enter-tag re-attribution.

13. **When frequency is a hard requirement, treat `better PnL but too few trades` as a failed iteration, not a success**
   - If the user requires a band like `30-60 trades/month`, evaluate every purification pass against that band explicitly.
   - A variant that improves from, say, `-0.14%` to `-0.03%` but drops from roughly `30/month` to `15/month` is **not** ready, even if the blotter looks cleaner.
   - In that case, do not roll back the whole idea. Preserve the quality improvement that worked (for example: restoring a usable short path or removing obviously bad longs), then selectively re-open only the least-bad mode needed to recover frequency.
   - Prefer this recovery order:
     1. restore some `trend` activity first
     2. keep the worst `pullback` path constrained if it was clearly the bigger loser
     3. only then loosen broader pair/side thresholds
   - Report both axes together: `quality delta` and `frequency delta`. The user needs to see whether the strategy got smarter, quieter, or both.

14. **If repeated micro-loosenings produce the exact same backtest blotter, you are not touching the active bottleneck**
   - Small edits to wick caps, body thresholds, HTF spread thresholds, or pair-side quantiles may appear meaningful but still leave the exact same 2-trade or 4-trade set.
   - When that happens, stop guessing from code alone and compare exported trades across runs. If the trade list is unchanged, the modified gate was not the marginal limiter on that window.
   - Practical implication: move to the next candidate bottleneck instead of stacking more tiny loosening patches in the same area.
   - In multi-timeframe FreqAI systems, the real limiter is often higher in the funnel: side enablement, prediction quantile/z-score policy, or the specific mode branch that ever becomes eligible — not the final wick/body cosmetic filter.

Reference: `references/freqai-frequency-floor-vs-quality-case.md` — concrete V6→V6.1 case where restoring short quality improved PnL but broke the required 30/month activity floor, and several micro-loosenings left the blotter unchanged, proving the active limiter sat earlier in the signal funnel.

## Freqtrade / FreqAI config pitfalls
- Freqtrade analysis configs still go through schema validation even for backtesting-only runs. If `api_server.jwt_secret_key` is present and too short, backtests fail before strategy logic runs. Treat this as a config validation fix, not a trading conclusion.
- For repaired/FreqAI analysis runs, keep the backtest invocation explicit about the model class (for example `--freqaimodel LightGBMRegressor`) when the config does not fully specify it.

## Strong default debugging sequence
1. Inspect same-candle losers.
2. Inspect entry timeout count.
3. Inspect custom entry pricing / order adjustment hooks.
4. Tighten entry conditions with explicit trend alignment and prediction margin.
5. Re-backtest same window.
6. Split results by pair and side.
7. Test BTC-only / dominant-pair-only if one asset is causing most losses.
8. For FreqAI short-horizon systems, test horizon repair before major filter loosening.
9. If trade count rises but expectancy collapses, retreat to the best isolated subset and only then consider dual-model redesign.

## Good output format for future sessions
When reporting strategy triage results, present:
- What changed in code
- What tests were added/updated
- Baseline metrics
- New metrics
- Improvement deltas
- What problem seems solved
- What problem remains
- Recommended next action ranked by leverage

## References
- `references/freqtrade-entry-quality-case.md` — concrete case of reducing loss by removing pullback entry logic, adding stricter entry filters, and analyzing pair-level residual damage.
- `references/freqai-structure-filter-and-side-edge-audit.md` — concrete pattern for FreqAI triage: reduce same-candle/execution artifacts first, then audit long vs short edge separately and choose asymmetric-label/classification follow-ups.
- `references/freqai-pair-policy-and-zero-trade-relaxation-case.md` — concrete pattern for FreqAI repair when one pair/side is biased, the first strict V4 pass yields zero trades, and the correct next step is asymmetric pair policy plus controlled gate relaxation rather than immediate dual-model redesign.
