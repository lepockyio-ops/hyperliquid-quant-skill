# FreqAI V6.1 one-trade window vs target-frequency case

Use this reference when a repaired FreqAI variant returns to "barely
trading" after a previous frequency-unlock attempt over-corrected, and
the user wants to restore activity without re-introducing the toxic
mode found earlier.

## Session pattern captured

Backtest under review (V6.1 frozen variant):

- timeframe: 5m
- window: `20260510 14:20 JST -> 20260515 09:00 JST` (~5 active days)
- trades: 1
- direction split: 1 long / 0 short
- win rate: 100% (N=1)
- total profit: +0.11% (+0.1065 USDC on 1000 wallet)
- max drawdown: 0
- avg hold duration: 10h15m
- user target: 30-60 trades/month, hold 1-5h, monthly net return >= +5%

## Diagnosis

Two simultaneous mismatches must be named explicitly before deciding what
to relax:

1. **Activity collapse.** 1 trade in ~5 days projects to ~6 trades/month,
   ~10x below the 30-60/month floor.
2. **Hold-time overshoot.** 10h15m vs target 1-5h; the single trade ran
   ~2x the upper bound of the intended profile.

The 100% win rate is statistically meaningless at N=1. Quality cannot be
evaluated until activity reaches a meaningful sample size.

## What previous repair passes already settled

From earlier session refs in this skill:

- V5 (`freqai-frequency-unlock-and-mode-pruning.md`): unlocked to ~50
  trades in 61 days, but `short_direct` produced ~0% win rate with 23
  trades. Toxic mode identified.
- V6 (`freqai-frequency-floor-vs-quality-case.md`): 4 trades / 7 days,
  all BTC longs, all exits trailing_stop_loss. Quality bad, activity
  low.
- V6.1 (frozen, this case): 2 trades / 1 ETH short pullback in earlier
  session, then 1 BTC long in the May 10-15 window. Quality acceptable,
  activity broken.

The pattern repeats: thresholding alone bounces between under-trade and
over-trade. The shape of the entry surface needs to change.

## Recommended workflow (V6.2)

Follow this order rather than reaching first for threshold tuning:

### 1) Replace the AND-chain with must + scored vote

For each existing entry tag (`direct`, `pullback`, `trend`):

- Reduce hard "must" conditions to 2-4 items: factor freshness, higher
  timeframe alignment, ADX floor.
- Convert the remaining 4-6 historic AND conditions into 0/1 votes.
- Require `score >= VOTE_THRESHOLD` (start at 3 of 6 for standard, 4 of
  6 for stricter modes).

This breaks the multiplicative collapse of the AND chain. Empirically
on 5m bars, V6.1 fired at ~0.07% per bar; a 3-of-6 vote on the same
voter set typically lands in the 0.35-0.60% band, i.e. 5-10x.

### 2) Carry forward the V6 mode attribution

Long-side modes (long_direct, long_trend) get the standard threshold.
Long_pullback and all short_* modes get threshold +1 so that the
historically weaker buckets must clear a higher bar. Avoid reopening
`short_direct` at the standard threshold; that was the V5 toxic mode.

### 3) Fix the hold-time mismatch with explicit guards

V6.1's 10h15m duration is not solved by frequency changes; it needs an
exit-side guard:

- ATR multiplier 1.5 (5m-appropriate; v3-v7 default 2.5 was 1h-tuned).
- Partial TP ladder: +1R exit 50%, +1.5R exit further 30%, trail rest.
- Hard time exit: `hold > 5h` + `profit < +0.3%` -> exit; `hold > 8h`
  unconditional exit.

The 5h productivity guard is the most direct fix for the 10h15m
overshoot. It also frees capital sooner, indirectly helping hit the
30-60/month floor.

### 4) Compress fees so monthly target math is feasible

At 60 trades/month with taker-taker round-trip ~5 bp, fee drag is
~3.0% of equity. With maker-taker ~4 bp it drops to ~2.4%. The
strategy class should set `order_time_in_force = {"entry": "PO"}` and
`order_types["entry"] = "limit"`. Abandon unfilled entries within the
candle via `unfilledtimeout.entry = 5` minutes.

### 5) Rotate FreqAI identifier

V6.2 changes the engineered feature set (new voter columns) and the
entry surface. Reusing the V6.1 FreqAI identifier risks the column
mismatch failure from core lesson 19. Set
`freqai.identifier = "HL_v6_2"` (or similar) and let FreqAI rebuild.

## Acceptance criteria for V6.2

1. Trades / month inside `[30, 60]` over a window of >= 60 trades or
   >= 30 trading days, whichever comes first.
2. Avg hold duration between 1h and 5h.
3. Aggregate expectancy >= 0 net of fees over the full window.
4. No single enter_tag has >30% of trade count combined with a near-zero
   win rate (the V5 short_direct pattern).
5. If FreqAI is active, PSI on key external factors stays below 0.2
   during the shadow window.

## What not to do

- Do not relax thresholds globally to chase frequency without first
  switching to the must + scored-vote shape. That recreates V5.
- Do not blame FreqAI when activity is sub-floor. The AND-chain
  collapse on indicator columns is the more likely root cause.
- Do not raise leverage to bridge the +5% target before activity is
  inside [30, 60]. Sample-size starvation will not be fixed by larger
  position sizes.

## See also

- `freqtrade-hyperliquid/references/v6_2-scored-vote-frequency-rebalance.md` -
  the durable workflow this case study supports.
- `freqai-frequency-unlock-and-mode-pruning.md` - V5 toxic-mode source.
- `freqai-frequency-floor-vs-quality-case.md` - V6 baseline observation.
- `freqai-signal-funnel-analysis.md` - per-stage funnel diagnostics for
  measuring where the multiplicative collapse happens.
