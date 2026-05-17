# FreqAI frequency floor vs quality case

## Context
User constraint stack for this class of session:
- preserve all entry modes (`direct / pullback / trend`)
- keep monthly trade frequency in the `30-60/month` band
- continue along the FreqAI path rather than reverting to pure rule filters
- optimize for better market reading, signal quality, and entry quality

## Baseline examined
Window: `20260508-20260515`

Baseline V6:
- trades: 4
- monthly pace: ~30/month
- total profit: `-0.14%`
- avg duration: `5m`
- blotter pathology: all 4 trades were BTC longs; every trade exited via `trailing_stop_loss`
- mode attribution:
  - `freqai_long_trend_v6`: 2 trades, less bad
  - `freqai_long_pullback_v6`: 2 trades, worse average loss

Key lesson from the baseline: the system had enough activity, but the activity was low-quality and one-sided.

## V6.1 repair direction that helped
The useful direction was **signal purification with side rebalance**:
- stronger geometry filters
- stronger FreqAI conviction gates
- selective restoration of short-side eligibility
- explicit enter-tag review after each run

A candidate V6.1 run improved quality materially:
- trades: 2
- monthly pace: ~15/month
- total profit: `-0.03%`
- composition: 2 ETH short pullbacks, 1 win / 1 loss

Interpretation:
- quality improved
- short side became at least somewhat usable
- worst long-side noise was suppressed
- but the hard frequency floor was broken

## Durable debugging lesson
Do **not** mark this kind of iteration complete.
A lower-loss variant that falls under the required activity floor is still a failed candidate for this user.

Use this decision rule:
1. keep the quality lesson that worked
2. identify which surviving mode is least bad from enter-tag stats
3. restore activity by reopening that mode first
4. avoid re-opening the historically worse mode just to hit a count target

In this case, the next default move is:
- try to restore some `long_trend` activity first
- keep `long_pullback` tighter because it was the worse loser in the baseline
- only after that loosen broader pair/side thresholds

## Another important pattern from the session
Several micro-loosenings changed code but did **not** change the resulting trade set.
Examples of edits that can easily be inactive on a given window:
- wick ratio caps
- trend body thresholds
- HTF spread thresholds
- modest pair-specific quantile relaxation

If the exported blotter remains identical after those edits, conclude:
- the touched filter is not the active bottleneck on that window
- the real limiter is earlier in the funnel (often side policy, prediction gate, or mode eligibility)

## Practical reporting format
When this happens, report two verdicts separately:
- **quality verdict**: improved / not improved
- **frequency verdict**: within required band / below band / above band

Only call the iteration successful if both pass.
