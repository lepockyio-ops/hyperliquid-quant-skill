# V6.2 scored-vote frequency rebalance

Use this reference when a `HLAdaptiveTrendFreqAI*` variant is producing roughly
the right *quality* (positive expectancy on its tiny trade set) but is firing
far below the trader-required activity floor (target 30-60 trades/month, hold
1-5h, monthly return aim >= +5%).

## Session pattern captured

V6.1 baseline backtest:

- timeframe: 5m
- window: `20260510 14:20 JST -> 20260515 09:00 JST` (~5 active days)
- trades: 1
- direction split: 1 long / 0 short
- win rate: 100% (N=1, statistically meaningless)
- total profit: +0.11% (+0.1065 USDC on 1000 wallet)
- max drawdown: 0
- avg hold duration: 10h15m

Two problems are visible without needing more data:

1. **Activity floor broken.** 1 trade in 5 days projects to ~6 trades/month,
   ~10x below the user's stated 30-60/month target.
2. **Hold-time alignment broken.** Average duration 10h15m vs target 1-5h.
   Even the single trade is twice the upper bound of the intended holding
   profile.

These are funnel problems, not edge problems. Quality cannot be evaluated on
N=1, and the strategy must first be rebalanced to fire often enough to
generate a measurable distribution.

## Why this matters

V6 -> V6.1 trajectory in this stack already showed the frequency vs. quality
tension:

| variant | days | trades | monthly pace | total profit | notes                                  |
| ------- | ---- | ------ | ------------ | ------------ | -------------------------------------- |
| V4      | ~89  | 2      | ~0.7         | -            | far below activity floor                |
| V5      | ~61  | 50     | ~25          | negative     | unlocked, but `short_direct` toxic     |
| V6      | ~7   | 4      | ~17          | -0.14%       | one-sided BTC long, all trailing-stops |
| V6.1    | ~5   | 1      | ~6           | +0.11%       | quality OK, activity collapsed         |

Each repair pass either over-tightens (V4, V6.1) or over-loosens (V5).
V6.2's purpose is to step off this oscillation by changing the *shape*
of the entry rule, not just the thresholds.

## Why purely tightening or loosening keeps failing

The current entry surface for any single mode is an AND-chain across 6-8
conditions: trend confirmation, geometry, FreqAI conviction, factor gates,
candle reversal, etc.

When the chain is AND across `k` independent conditions each with firing
probability `p_i`, the joint firing rate is the product. Lowering one
threshold moves only one factor; the multiplicative collapse remains.

On a 5m timeframe with 1440 candles/5 days, V6.1 fired once, so the per-
candle firing rate ~= 0.07%. To hit 30-60 trades/month (~8640 candles/month)
the per-candle rate must reach 0.35-0.70%, i.e. **5-10x the current rate**.
Single-threshold relaxations cannot bridge that gap without re-introducing
the V5-style toxic modes.

## Durable workflow for V6.2

Replace the per-mode AND-chain with a **must-conditions + scored-vote**
surface inside each existing entry tag (`direct`, `pullback`, `trend`).
The mode taxonomy and tag attribution are kept intact so V5/V6 enter_tag
diagnostics still apply.

### Step 1 - define hard "must" conditions

These are non-negotiable; missing any one must veto entry regardless of
score. Keep this list small (2-4 items):

- `factor_freshness_ok` (per the wall-clock-vs-candle-time rule, evaluated
  against the candle's timestamp during backtest).
- `higher_timeframe_trend_aligned` (5m main, 1h or 4h confirmation in the
  direction of intended trade).
- `adx > ADX_FLOOR` (relaxed: 18 on 5m, not 22 from the 1h v3-v7 defaults).

### Step 2 - score the remaining filters

Convert the rest of the historic AND conditions into a 0/1 vote. Sum the
votes and require `score >= VOTE_THRESHOLD`. Suggested vote items:

- EMA fast strictly above slow (replaces single-bar `crossed_above`).
- 5m close above EMA fast (trend confirmation persistence).
- ATR percentile below q60 over 100 bars (don't enter into extreme vol).
- Cross-exchange funding diff inside band (relaxed: 5 bp instead of 3 bp).
- FreqAI predicted direction aligned (when FreqAI is enabled).
- Liquidation cluster proximity favorable (if liq factor active).

`VOTE_THRESHOLD = 3` of 5-6 voters is a reasonable starting point. Concrete
tuning is in `strategy-templates/HLAdaptiveTrendFreqAI_v6_2.py`.

### Step 3 - apply mode-asymmetry from V6 attribution

V6 enter-tag stats showed `short_direct` as the toxic mode and BTC long
trailing-stop dominance. V6.2 starts by re-opening the **least-bad** modes
first:

- `freqai_long_trend_v6_2`: re-enabled at standard score threshold (3 of 6).
- `freqai_long_pullback_v6_2`: re-enabled at score threshold +1 (4 of 6).
- `freqai_long_direct_v6_2`: enabled at standard threshold (3 of 6).
- `freqai_short_*_v6_2`: re-enabled at score threshold +1 (4 of 6). The
  long-side bias matches BTC/ETH funding cost asymmetry and the V6/V6.1
  observation that short side under-performed.

### Step 4 - exit-side fixes so hold duration matches target

Three independent exit guards run together:

1. ATR-based custom stoploss tightened to `1.5 * ATR` (5m-appropriate; v3-v7
   default 2.5 was 1h-tuned).
2. Partial take-profit ladder via `adjust_trade_position`:
   `+1.0R` -> exit 50%, `+1.5R` -> exit further 30%, leave 20% to trail.
3. Hold-time guard via `custom_exit`:
   - `hold > 5h` and `current_profit < +0.3%` -> exit with tag `time_exit_unproductive`.
   - `hold > 8h` (regardless of PnL) -> exit with tag `time_exit_hard_cap`.

The hold-time guard is what most directly fixes the 10h15m -> 1-5h
mismatch. It also frees capital sooner for the next signal so the funnel
output can hit the 30-60/month band even if win rate is modest.

### Step 5 - execution fee compression

At the target frequency the fee surface dominates expectancy. Math used to
size V6.2:

- HL maker 0.015% / taker 0.025%.
- Round-trip taker-taker: 5 bp. Round-trip maker-taker: 4 bp.
- 60 trades/month * 4 bp = 240 bp = 2.4% fee drag.
- Required gross monthly edge for +5% net: ~7.5% gross.
- Per-trade gross edge needed: 7.5% / 60 = 12.5 bp. Achievable but the
  margin disappears if entries are taker-taker.

V6.2 entries default to `limit` orders with `time_in_force = "PO"`
(post-only). If the limit does not fill within the candle, the entry is
abandoned and re-evaluated next candle. The exit-side legs and the
exchange-side stoploss-limit follow the existing v7.1 wiring described in
core lesson 12 and `references/live-order-capabilities.md`.

### Step 6 - explicit candle-time freshness check

All factor-freshness comparisons must use the candle's own timestamp during
backtest (core lesson 10). The V6.2 template includes a `_now_for_freshness`
helper that returns `dataframe.iloc[-1].date` during backtest and
`pd.Timestamp.utcnow()` only when `self.dp.runmode in (RunMode.LIVE, DRY_RUN)`.

## Expected ranges after V6.2 is applied

Single-strategy, single-symbol, 5m main timeframe, BTC + ETH long-biased:

| metric                | V6.1 observed     | V6.2 expected band      |
| --------------------- | ----------------- | ----------------------- |
| Trigger rate per bar  | ~0.07%            | 0.35-0.60%              |
| Trades / month        | ~6                | 30-50                   |
| Avg hold duration     | 10h15m            | 2-4h                    |
| Win rate              | 100% (N=1)        | 50-60%                  |
| Per-trade gross edge  | unknown           | 5-15 bp                 |
| Per-trade net edge    | unknown           | 1-11 bp (after fees)    |
| Monthly net return    | unknown           | -1% to +3%              |

`+5%` is a stretch goal; reaching the upper end of the band requires either
multi-symbol concurrency (BTC + ETH + SOL with low correlation), additional
edge from a second strategy variant (mean-reversion in low-vol regimes), or
leverage uplift beyond the current ladder.

## When to declare V6.2 acceptable

Use the same activity / quality joint check as `freqai-frequency-floor-vs-quality-case.md`:

1. **Trades / month** lands inside `[30, 60]` over a window with >= 60
   trades (or >= 30 trading days, whichever comes first).
2. **Avg hold duration** between 1h and 5h.
3. **Aggregate expectancy** >= 0 net of fees over the full window.
4. **Enter-tag attribution**: no single mode produces a near-zero win rate
   with >30% of the trade count (the V5 `short_direct` pattern).
5. **PSI / drift**: if FreqAI is active, the shadow-period feature PSI is
   below 0.2 across all key external factors.

If (1) and (2) are met but (3) is not, fix exits and execution before
adding more activity. If (3) is met but (1) is not, prune one toxic mode
first per the existing playbook in
`freqai-frequency-unlock-and-mode-pruning.md` rather than blanket
re-tightening.

## See also

- `freqai-frequency-floor-vs-quality-case.md` - the principle V6.2 is built on.
- `freqai-frequency-unlock-and-mode-pruning.md` - the toxic-mode pruning rule.
- `live-order-capabilities.md` - exchange-side stoploss + post-only entry behavior on Hyperliquid.
- `factor-gating-backtest.md` - the freshness-against-candle-time rule that V6.2 re-applies in its scoring layer.
- `strategy-templates/HLAdaptiveTrendFreqAI_v6_2.py` - concrete code template for the scored-vote model and exit guards.
- `strategy-templates/config_v6_2_snippets.jsonc` - config snippets for post-only entry, stoploss-on-exchange, and order_types alignment.
