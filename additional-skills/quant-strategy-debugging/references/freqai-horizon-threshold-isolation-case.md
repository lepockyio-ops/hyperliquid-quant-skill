# FreqAI horizon / threshold / isolation case

Use this reference when a short-horizon FreqAI strategy shows one or more of these pathologies:
- 1-5h intended holding period, but the label horizon is much longer (for example 24 candles on 1h bars)
- almost all trades come from one pair
- one side barely trades or all one-side trades lose
- loosening filters increases trade count but makes PnL sharply worse

## Starting symptoms
- Baseline backtest: 10 trades over ~3 months, all on ETH, total profit about -0.94%.
- Average holding time was short (~1h48m), so the prior `label_period_candles = 24` was mismatched to actual trade duration.
- Diagnosis before changes showed BTC produced almost no threshold hits while ETH produced many more long hits.
- External-factor freshness issues existed in parallel, but the immediate repair loop focused on label/threshold/structure sequencing.

## Minimal-repair moves that were implemented
1. Shorten `label_period_candles` from 24 to 6.
2. Replace a single global decision threshold with pair-aware and side-aware threshold logic.
3. Build isolation configs for:
   - all pairs
   - BTC-only
   - ETH-only
   - long-only
   - short-only
4. Slightly loosen structure filters only after the horizon/threshold repairs.
5. Keep repaired backtests explicit about the FreqAI model class (`--freqaimodel LightGBMRegressor`).
6. Fix config-schema blockers before interpreting results; in this case `api_server.jwt_secret_key` needed to satisfy schema min length for analysis configs.

## Observed results after the first repair loop
- All-pairs V3: 35 trades, -3.25%
- BTC-only: 13 trades, -0.90%
- ETH-only: 23 trades, -2.45%
- Long-only: 15 trades, -1.41%
- Short-only: 20 trades, -1.83%

## What these results mean
- **Horizon repair worked for trade generation**: trade count rose and BTC started participating.
- **Threshold repair worked for pair coverage**: BTC was no longer effectively dead.
- **Model edge was still weak**: the added trades were mostly low-quality; expectancy got worse overall.
- **ETH remained the major drag**: isolation showed ETH contributed most of the damage.
- **Long and short both underperformed, but differently**: this justified considering side-specific targets or models next, rather than treating the strategy as symmetric.
- **Structure loosening should not be treated as a free win**: more trades plus worse expectancy is evidence against broad loosening.

## Reusable lesson
For short-horizon FreqAI systems, use this sequence:
1. horizon repair
2. pair/side threshold calibration
3. pair/side isolation backtests
4. only then decide whether to loosen filters more or split long/short targets/models

If the first pass increases activity but makes expectancy materially worse, do not keep broadening. Step back to the best-isolated subset (for example BTC-only) and continue refining there.
