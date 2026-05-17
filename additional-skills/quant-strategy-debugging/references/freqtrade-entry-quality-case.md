# Freqtrade entry-quality loss-reduction case

## Situation
A Hyperliquid/Freqtrade strategy was materially losing money over a fixed backtest window. The first pass focused on whether the main damage came from execution pathology versus the model itself.

## Baseline symptoms
- Heavy overall loss
- Very low win rate
- Many same-candle trades with strongly negative aggregate PnL
- Large entry-timeout count
- ETH responsible for most of the losses

## Hypotheses
1. Pullback-style custom entry pricing was causing poor fills, missed fills, or getting tagged only on weak candles.
2. Entry conditions were too permissive: prediction threshold alone was not enough.
3. Aggregate bad performance might be concentrated in one asset rather than the whole strategy being equally broken.

## Implemented changes
### Execution change
- Removed ATR-based pullback offset from custom entry price.
- Stopped re-anchoring submitted entry orders back to a pullback price via order-adjustment logic.

### Signal-quality change
For new entries, require more than model prediction alone:
- prediction edge above threshold, not merely above target
- EMA short/long alignment
- ADX strength gate
- price displacement relative to higher-timeframe EMA using ATR buffer
- auxiliary factor sanity checks (funding / whale flow directionality)

## Tests that were valuable
- Entry price returns proposed_rate directly instead of pullback offset.
- Entry-order adjustment does not push entry orders back to a synthetic pullback price.
- Entry-trend logic requires prediction edge plus trend alignment.
- Existing factor-gating and retrain-contract tests continue to pass after behavioral changes.

## Result pattern
The strategy remained lossmaking, but several pathologies improved materially:
- trade count dropped sharply
- win rate improved
- same-candle loss burden roughly halved
- entry timeouts dropped to zero
- total loss shrank substantially

## Most important interpretation
This kind of outcome means the first-layer problem was likely **entry execution + weak entry filtering**, not solely the model. However, if the strategy is still negative after the fix, the remaining issue is now more likely **true lack of edge or asset-specific mismatch**.

## Residual localization lesson
After the first pass, split results by pair. In this case, BTC was near flat while ETH still drove most losses. That suggests the next highest-leverage move is often:
1. disable the dominant losing pair in live/dry-run first, or
2. create stricter pair-specific thresholds before broader redesign.

## Reusable heuristics
- If same-candle stopouts are a major PnL sink, inspect entry mechanics before touching exits.
- If entry timeouts are high, treat custom entry pricing as suspect until proven otherwise.
- When one pair dominates losses, run pair-isolated backtests before concluding the whole strategy has no edge.
- A reduction in losses is progress, but do not label it success unless expectancy turns positive or nearly so under realistic constraints.
