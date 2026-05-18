---
name: freqtrade-hyperliquid
description: Operate and debug a Freqtrade + Hyperliquid futures workflow, including local parquet history, rendered runtime config, factor-gated strategies, and backtesting.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [freqtrade, hyperliquid, trading, backtesting, parquet, config, debugging]
    related_skills: [systematic-debugging, implementation-readiness-audit]
---

# Freqtrade + Hyperliquid Workflow

## When to use

Use this skill when the task involves any of:
- Running Freqtrade against Hyperliquid futures.
- Converting non-Freqtrade OHLCV parquet into Freqtrade backtest data.
- Debugging factor-gated strategies that read external parquet inputs.
- Rendering runtime config from `.env` into a concrete config file.
- Verifying backtests, dry-run startup, webhook/Telegram notification wiring.

## Core lessons

1. **Do not assume Freqtrade will expand `${VAR}` placeholders.** Render a concrete runtime config first.
2. **Hyperliquid raw OHLCV parquet is not automatically in Freqtrade layout.** Convert and store it with Freqtrade's own data handler conventions.
3. **For futures OHLCV, files must live under `.../data/<exchange>/futures/` and use Freqtrade pair/timeframe naming.**
4. **Factor-gated strategies can backtest to zero trades simply because factor history is absent or stale.** Verify factor coverage before judging the strategy.
5. **Webhook/Telegram enablement should be derived from whether credentials exist, not hardcoded.**
6. **Backtesting does not need an extra `--trading-mode futures` flag when the config already sets futures mode; passing unsupported flags can fail.**
7. **`show-config` / `list-data` should use the rendered runtime config, not the unresolved template config.** Template `${VAR}` placeholders can still fail schema checks (for example JWT min-length).
8. **Do not assume a config renderer's CLI flags are real just because you invoked them.** If the render script is custom, inspect it for hardcoded template/output paths or ignored `--template` / `--output` arguments, then verify the expected destination file was actually written.
9. **For Discord webhook verification, prefer a direct `curl` POST and expect HTTP `204` from Discord.** Some generic Python `urllib` probes may return `403` even when the webhook itself is valid.
10. **Backtest-time factor freshness must be evaluated against candle time, not wall-clock time.** If a strategy computes factor staleness with `pd.Timestamp.now()` / `utcnow()`, historical backtests can be silently forced to `0 trades` even when factor parquet fully covers the timerange.
11. **A full workflow check should include a short dry-run boot, not only backtesting.** For FreqAI stacks, confirm the bot reaches `RUNNING`, API/webhook modules start, pairlist refresh completes, and the first training/download cycle begins without crashing.
12. **For Hyperliquid futures in Freqtrade, `stoploss_on_exchange` is supported, but this is not a full native bracket/OCO workflow.** Treat it as: entry order fills first, then Freqtrade places/maintains an exchange-side stop-loss-limit order.
13. **When reviewing live order behavior, separate three loops:** position adjustment cadence (`process_throttle_secs`, often 5s), exchange-side stoploss refresh cadence (`stoploss_on_exchange_interval`, often 60s), and candle-based signal generation. They are not the same thing.
14. **For pullback entries, do not assume Freqtrade only market-chases on the polling loop.** Limit-style workflows can be implemented with `custom_entry_price()`, `adjust_order_price()`, `check_entry_timeout()`, and `unfilledtimeout`.
15. **For live position management, distinguish callback cadence by capability.** `adjust_trade_position()` can react on the bot loop (for example every 5s when `process_throttle_secs=5`), but order repricing via `adjust_order_price()` is still candle-bound and exchange-side stoploss refresh remains governed separately by `stoploss_on_exchange_interval`.
16. **Position adjustment must be explicitly enabled in the strategy class.** `adjust_trade_position()` will never run unless `position_adjustment_enable = True`; use `max_entry_position_adjustment` to cap additional entries so a 5s loop cannot pyramid indefinitely.
17. **Custom pullback pricing needs config support, not just strategy code.** If `custom_entry_price()` returns deeper retracement prices, align `custom_price_max_distance_ratio` and timeout settings (`check_entry_timeout()` / `unfilledtimeout`) so Freqtrade does not clamp or cancel the order earlier than intended.
18. **FreqAI live/dry-run startup needs both the right runtime flag and the right image/dependencies.** A strategy with a valid `freqai` block can still exit immediately if you forget `--freqaimodel <ModelClass>` (for example `LightGBMRegressor`), and Docker deployments should use a FreqAI-capable image such as `freqtradeorg/freqtrade:stable_freqai`.
19. **If FreqAI crashes with a feature-pipeline column mismatch against an existing model cache, rotate the `freqai.identifier`.** Reusing an old identifier after changing engineered features, pair set, or feature ordering can make FreqAI load incompatible historic predictions / model artifacts; bump to a new identifier so it rebuilds from scratch.
20. **Early FreqAI live warnings are not always failures.** During first boot it is normal to see messages like `No model ready for <pair>, returning null values to strategy` before the first training cycle finishes; confirm the bot remains in `RUNNING` and that training starts rather than treating the warning alone as a crash.
21. **For FreqAI strategies, do not assume features merged in `populate_indicators()` are part of model training.** The training/prediction dataframe is built from the `feature_engineering_*` hooks; if you want external parquet factors (for example Vibe funding / liquidation / onchain fields) to become model features, merge them inside `feature_engineering_standard()` or another `feature_engineering_*` path and then verify they appear in `training_features_list`.
22. **If external factors appear in strategy gating but not in FreqAI metadata, the model is not actually using them.** Verify the latest model metadata after retraining instead of inferring from live strategy columns alone.
23. **When newly added external factors disappear after retraining, inspect feature-selection logs before blaming the merge.** Filters such as `VarianceThreshold` can legitimately remove low-variance factors; this means the wiring is correct but the factor series is too flat or uninformative in the chosen training window.
24. **When the user asks for a trader-facing explanation, describe the system as roles and workflows, not classes and callbacks.** Explain the live setup as: execution engine (Freqtrade), model/decision layer (FreqAI), external factor feed (Vibe parquet), and notification/observation loops. Make it explicit when a 15m Discord scanner is only an alerting line and not the same thing as the main execution timeframe.
25. **For short/mid-term Hyperliquid workflows, keep 'decision cadence' separate from 'management cadence' and 'alert cadence'.** Example from this stack: main entries are still 1h with 4h confirmation, internal order/position management may loop every 5s, and a discretionary Discord market scan may publish every 15m. Users will otherwise assume the 15m alert is the trading engine itself.
26. **Treat Vibe-Trading as a research/factor supply layer unless it is explicitly wired into execution.** In this workflow it contributes funding / liquidation / onchain parquet and can veto entries via factor gates, but it does not place orders or manage positions; Freqtrade remains the executor.
27. **A lightweight discretionary scan can coexist beside the main bot.** A cron-driven script that fetches Hyperliquid data, scores a setup, formats a fixed trader-readable message, and posts to a Discord webhook/channel is a valid 'observation line' even when the main bot keeps a different timeframe and strategy.
28. **A near-zero-trade FreqAI variant cannot be evaluated by win rate.** When monthly trade count is far below the user-stated activity floor (e.g. 1 trade in 5 days vs target 30-60/month), the diagnosis is a funnel-shape problem, not an edge problem; quality metrics on N<10 are statistically meaningless.
29. **AND-chains of 6-8 independent entry conditions collapse multiplicatively.** Single-threshold relaxations cannot bridge a 5-10x firing-rate gap without re-introducing previously identified toxic modes; switch the entry surface to a must-conditions + scored-vote shape instead.
30. **Hold-duration overshoot is fixed by exit-side guards, not by tighter entries.** When average duration runs 2x the trader's target band (e.g. 10h vs 1-5h), add an unproductive-time exit (e.g. hold > 5h and profit < +0.3%) and a hard-cap exit (e.g. hold > 8h), instead of further tightening entry conditions.
31. **Post-only limit entries are necessary at 30-60 trades/month frequency on Hyperliquid.** Round-trip taker-taker fees are ~5 bp; maker-taker drops that to ~4 bp; at 60 trades/month the difference is ~0.6% of equity per month, which dominates the +5% monthly target math.
32. **Mode asymmetry from earlier sessions must persist across repair passes.** If V5 attribution flagged `short_direct` as the toxic mode, V6.2's scored-vote layer keeps short_* thresholds strictly above long_* (e.g. require score >= 4 of 6 for shorts vs 3 of 6 for longs); do not re-open the historically worse mode at the standard threshold just to hit the activity floor.

## Standard workflow

### 1) Render a runtime config

Start from a template config and render secrets from `.env` into a concrete file.

Required behaviors:
- Replace `${VAR}` placeholders before any Freqtrade command.
- Set `webhook.enabled=true` only if `DISCORD_WEBHOOK_URL` is present.
- Set `telegram.enabled=true` only if both token and chat_id are present.
- Leave the template safe to commit; keep runtime config mode 600.
- If the renderer is a custom script, verify it actually honors its advertised CLI parameters instead of writing to hardcoded paths.
- Prefer a renderer CLI with explicit `--env`, `--template`, and `--output` arguments so separate runtime configs (for example `config.runtime.freqai.json`) are first-class rather than side effects.

Verification:
- `freqtrade show-config --config <runtime-config>` succeeds.
- JWT secret length passes schema validation.
- The file you intended to render is the one whose mtime/content changed; do not trust the command line alone.

### 2) Convert Hyperliquid parquet into Freqtrade OHLCV storage

If historical data was fetched externally:
- Normalize to columns: `date, open, high, low, close, volume`.
- Convert `date` to UTC and floor to milliseconds.
- Sort by date and drop duplicate timestamps.
- Use Freqtrade's parquet data handler naming and storage layout.

Expected destination shape:
- `<datadir>/futures/BTC_USDC_USDC-1h-futures.parquet`
- `<datadir>/futures/ETH_USDC_USDC-4h-futures.parquet`
- etc.

Verification:
- Run `freqtrade list-data --config <runtime-config> -d <datadir> --data-format-ohlcv parquet --show-timerange`
- Confirm pair/timeframe rows appear with sensible timeranges.

### 3) Check factor parquet coverage before backtesting

For strategies that gate entries on external factors:
- Inspect required columns and SLA windows in the strategy.
- Ensure factor parquet exists for every pair.
- Ensure factor history spans the backtest window, not just "current time" rows.
- If using mock factors for pipeline validation, generate rows aligned to the historical candle dates.

Common pitfall:
- A strategy can be perfectly healthy yet produce **0 trades** because factors are unavailable for historical candles.
- Another common cause is **using wall-clock freshness checks inside factor gating**. In backtests, compare factor `as_of` to the relevant candle / dataframe time instead of the current real time.

### 4) Run the backtest

Use the rendered runtime config and explicit datadir.

Typical command shape:
```bash
freqtrade backtesting \
  --config user_data/config.runtime.json \
  --userdir user_data \
  --strategy HLAdaptiveTrend \
  -d user_data/data/hyperliquid \
  --data-format-ohlcv parquet \
  --timerange 20251018-20260515 \
  --enable-protections \
  --export trades \
  --backtest-directory user_data/backtest_results
```

Verification:
- Strategy loads.
- Informative timeframe data loads.
- Backtest result zip/meta files are written.
- If trades are zero, distinguish **pipeline success / strategy silence** from a true system failure.

### 5) Do a short dry-run boot check

After backtesting passes, boot the real bot path briefly in `dry_run`:
```bash
timeout 45s freqtrade trade \
  --config user_data/config.runtime.freqai.json \
  --strategy HLAdaptiveTrendFreqAI
```

Verify in logs that:
- the bot reaches `RUNNING`
- API server starts if enabled
- webhook / RPC modules initialize if enabled
- pairlist refresh completes
- FreqAI begins its initial train/predict cycle without crashing

Interpretation notes:
- A timeout exit from `timeout 45s ...` is expected if startup was healthy and you intentionally killed it.
- Missing backtest-time `funding_rate` files are not necessarily a live-boot blocker; dry-run may auto-download them during startup. Record whether startup recovered automatically.

### 6) Enable notifications

Before promising Discord automation:
- Check whether `DISCORD_WEBHOOK_URL` is actually present.
- If absent, report that the auto-notify path is prepared but cannot be enabled yet.
- After insertion, re-render runtime config and verify `webhook.enabled` became true.
- Verify the webhook with a direct POST and expect Discord HTTP `204`, for example:
```bash
curl -sS -o /tmp/discord_webhook_resp.txt -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  -d '{"content":"Freqtrade webhook test from Hermes"}' \
  "$DISCORD_WEBHOOK_URL"
```
- Treat `204` as success even if the response body is empty.

### 8) Explain the live setup in trader language when asked

Preferred framing for non-coders:
- **Execution engine:** Freqtrade is the trader that actually watches pairs, places/cancels orders, manages positions, and tracks PnL.
- **Decision/model layer:** FreqAI is the forecasting layer used by the strategy to estimate whether the next move is worth trading.
- **External factor layer:** Vibe-Trading supplies extra research signals (funding, liquidation, onchain) as parquet files; these act as filters / vetoes unless you explicitly wire them into the model.
- **Observation / alert layer:** Discord cron scans or webhook pushes are notifications for the operator; do not describe them as the execution engine unless they truly submit orders.

When multiple cadences exist, always label them separately:
- **Signal timeframe** (for example 1h entries with 4h confirmation)
- **Management cadence** (for example `process_throttle_secs=5` for open-order / position checks)
- **Alert cadence** (for example a 15m Discord market scan)

This prevents a common misunderstanding where a user sees a 15m scan and assumes the live bot itself now trades on 15m.


When the user wants Freqtrade to stay as the execution engine but behave less like "signal → immediate order" and more like a managed execution stack:

Strategy requirements:
- Set `position_adjustment_enable = True`.
- Set a finite `max_entry_position_adjustment`.
- Implement `custom_entry_price()` for pullback / passive entries.
- Implement `adjust_trade_position()` for partial take-profit, DCA, and size reductions.
- Implement `check_entry_timeout()` / `check_exit_timeout()` for order life-cycle control.
- Implement `order_filled()` if later callbacks need persistent per-trade context (entry ATR, entry snapshot, first-fill metadata).
- Implement `leverage()` explicitly in futures mode when pair-specific leverage caps matter.

Config requirements:
- Increase `custom_price_max_distance_ratio` if the intended pullback is wider than the default 2% clamp.
- Ensure global `unfilledtimeout` is not tighter than the strategy's custom timeout intent.
- Keep `order_time_in_force` explicit (`GTC` unless a tested exchange-specific reason says otherwise).

Verification:
- Add tests that prove partial TP happens before DCA when both could trigger.
- Add tests that DCA is blocked when factor gating or model validity fails.
- Add tests for timeout callbacks and `order_filled()` persistence hooks.
- Re-render runtime config after template changes, then run the focused strategy tests plus the existing factor-gating/config-render tests.


Do **not** re-enable a partial `freqai` block just to keep future options open. Either keep it fully absent, or enable it with a complete FreqAI setup.

Minimum prerequisites before enabling FreqAI:
- Install FreqAI dependencies and verify with `freqtrade list-freqaimodels`.
- Add a complete `freqai` config block with at least:
  - `enabled`
  - `train_period_days`
  - `backtest_period_days`
  - `identifier`
  - `feature_parameters`
  - `data_split_parameters`
- Use a dedicated FreqAI strategy file rather than mutating a working rule-based strategy in place.
- Implement the required FreqAI strategy hooks:
  - `self.freqai.start(...)` inside `populate_indicators()`
  - `feature_engineering_expand_all()`
  - `feature_engineering_expand_basic()`
  - `feature_engineering_standard()`
  - `set_freqai_targets()`
- Decide explicitly whether external Vibe factors belong:
  - outside the model as hard gates
  - inside the model as features
  - or both
- If factors should be model features, merge them in a `feature_engineering_*` hook (usually `feature_engineering_standard()`), not only in `populate_indicators()`.
- After retraining, inspect model metadata / `training_features_list` to prove the factors entered the ML pipeline.
- If the metadata includes them but feature-selection logs remove them, treat that as a data-quality / information-content problem (for example low variance), not a wiring failure.

Recommended first pass:
- Start with `LightGBMRegressor`.
- Use only OHLCV + standard technical features first.
- Keep external Vibe factors as model-external gating until the ML pipeline is proven.
- Save a separate template config and strategy skeleton for FreqAI experimentation.

## Debugging checklist

When dry-run or backtesting behaves oddly:

1. Read the current strategy file, not just old logs.
2. Verify config runtime values, especially:
   - `dry_run`
   - `exchange.name`
   - pair whitelist
   - webhook/telegram enabled flags
   - presence/absence of `freqai`
3. Run `list-data` to verify Freqtrade sees the expected parquet files.
4. Inspect factor parquet schemas and timeranges.
5. If trades are zero, instrument the analyzed dataframe and count how many rows are blocked by each gate (`_factors_available`, block reason, `do_predict`, prediction-vs-threshold comparisons, trend filters). Save row counts before changing thresholds.
6. For factor freshness logic, inspect whether the strategy uses wall-clock time (`now`/`utcnow`) instead of candle time. This is a high-probability root cause for historical backtests with external parquet factors.
7. For FreqAI + external factors, verify all three layers separately:
   - strategy dataframe contains the external columns
   - `training_features_list` in the latest model metadata contains the engineered `%` feature names
   - feature-selection logs did not immediately remove them (for example via `VarianceThreshold`)
8. Separate these failure classes:
   - config schema failure
   - data unreadable / wrong path
   - factor merge/type mismatch
   - wall-clock stale gating in backtests
   - strategy loads but generates no signals
   - factors wired into strategy but absent from the ML training pipeline

## Pitfalls

- **Do not treat old failing logs as proof of current failure** after the strategy has been patched.
- **Do not hardcode notification enablement** when credentials may be absent.
- **Do not judge a factor-gated strategy from a backtest that lacks historical factor rows.**
- **Do not assume Hyperliquid data downloaded outside Freqtrade is immediately backtest-ready.**
- **Do not rely on an inert `freqai` block with missing required fields**; schema validation can still fail.
- **Do not test commands like `show-config` or `list-data` against the unresolved template config** when runtime secrets are still placeholders.
- **Do not interpret `0 trades` as a pipeline failure by itself.** Once data, factors, and strategy loading are verified, `0 trades` is usually a signal-generation question.
- **Do not use `pd.Timestamp.now()` / `utcnow()` for factor-SLA gates that must work in historical backtests.** Use candle context (for example dataframe max `date`, current candle date, or merge tolerance) so backtests are judged against historical time, not today.
- **Do not relax prediction thresholds before proving gates are not already blocking all rows.** First quantify blockage by factor gating vs model thresholds.
- **Do not retrofit FreqAI into the live rule-based strategy first.** Create a parallel FreqAI strategy file and prove the ML path independently.
- **Do not assume external factors used by entry gating are automatically part of FreqAI training.** `populate_indicators()` can affect strategy logic while leaving the ML pipeline unchanged; engineered model features must come from `feature_engineering_*` hooks.
- **Do not call a factor-merge fix complete until you verify model metadata.** The durable check is whether the latest trained model lists the expected engineered features.
- **Do not confuse 'factor was merged' with 'factor survived feature selection'.** If `VarianceThreshold` or similar filters remove it, the next task is diagnosing factor variance/information content, not redoing the merge.
- **Do not reuse a possibly half-written FreqAI model directory for analysis reruns.** If backtesting finds old prediction files and then crashes on missing `*_metadata.json`, create a fresh temporary `freqai.identifier` (for example `*-analysis`) and rerun with side-effect integrations disabled.
- **Do not blame the model first when a 1h Hyperliquid backtest has many `trade_duration == 0` exits.** The combination of pullback `custom_entry_price()` plus custom stoploss / custom exit pricing can create same-candle fill-and-stop artifacts inside a single OHLC bar; isolate execution logic with an A/B rerun before changing features.

## Diagnosing weak FreqAI backtests

Use this mini-playbook before tweaking parameters:

1. Split results by pair, long/short side, and exit reason.
2. Inspect `trade_duration` distribution. A large cluster at `0` minutes is a strong sign of execution-path artifacts.
3. Compare same-candle exits versus all other trades; if same-candle trades dominate losses, simplify entry execution first.
4. When rerunning for diagnosis after feature/schema changes, use a temporary fresh `freqai.identifier` so stale backtest caches do not contaminate conclusions.
5. Confirm whether external factors only entered `training_features_list` or also survived preprocessing. Logs like `VarianceThreshold will remove ...` mean the code path is fixed but the factor still contributes no usable variance.
6. When the user requests a parameter-isolation validation (for example `max_open_trades=2`, `fee=0.045%`, or `unlimited leverage`), prefer a temporary validation subclass / runtime-config variant over mutating the main strategy. Keep the production strategy intact, write the experiment as a clearly named `*Validation` strategy + matching runtime config, and save outputs under a dedicated backtest prefix.
7. For `unlimited leverage` requests on Hyperliquid futures, do not assume the effective leverage is infinite or unchanged from the base strategy. If the strategy has local caps (for example `stake_cap_usd` / `leverage_hard_cap`), override them only in the validation subclass, then verify the **actual filled leverage from exported trades**. In this stack, the durable check is the backtest zip JSON `trades[*].leverage`, not the config text alone.
8. If a validation backtest suddenly turns from `0 trades` into all-losing trades, inspect the exported trades before blaming the model: pair distribution, long/short split, `trade_duration`, exit reasons, and leverage actually used. A strong red flag is `trade_duration == 0` for most or all trades, which usually means same-candle fill-and-stop behavior from the execution path rather than a clean multi-bar signal failure.

## References
- See `references/v6_2-scored-vote-frequency-rebalance.md` for the must-conditions + scored-vote entry surface, hold-time guard, and post-only execution pattern used to rebalance a frozen V6.1 variant back inside the 30-60 trades/month + 1-5h hold target.
- See `references/unlimited-leverage-validation.md` for the concrete pattern used in this stack: temporary validation subclass, runtime config clone, and post-backtest checks for actual leverage and same-candle stopout concentration.

- See `references/trader-facing-workflow.md` for a concise human explanation template covering: main execution line vs 15m alert line, Vibe-Trading's current role, and how to explain cadence differences to an experienced trader who does not code.
- See `references/freqtrade-hyperliquid-notes.md` for concrete file naming, command patterns, and session-tested validation steps.
- See `references/live-order-capabilities.md` for the render-script CLI contract (`--env/--template/--output`) and the live-order capability map: exchange-side SL vs native bracket behavior, position-adjust cadence, and pullback limit-entry patterns.
- See `references/freqai-enablement.md` for a compact checklist of FreqAI prerequisites, model choices, and Hyperliquid-specific rollout guidance.
- See `references/factor-gating-backtest.md` for the concrete zero-trade diagnosis pattern: wall-clock stale gating, instrumentation fields to count, and the smallest safe fix.
- See `references/freqai-external-factors.md` for the concrete diagnosis pattern when Vibe/external factors affect strategy gating but are missing from FreqAI training, plus the follow-up check for feature-selection filters like `VarianceThreshold`.
- See `references/freqai-backtest-cache-and-stopout-diagnostics.md` for the cache-isolation rerun pattern and the same-candle stopout analysis workflow.

