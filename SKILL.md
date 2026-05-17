---
name: hyperliquid-quant
version: 0.1.0
description: |
  Formulaic short-term quantitative trading on Hyperliquid perpetuals. Provides
  deterministic signal generation (funding filter, 15m liquidity sweep,
  1h/15m EMA context, ATR), strategy evaluation, risk guards, and order execution
  via the official Hyperliquid Python SDK. The agent MUST follow the
  Decision Protocol below — no discretionary trades.
license: MIT
runtime: stdio
command: hl-quant-mcp
mcp_tools:
  - hl_get_account_state
  - hl_get_market_data
  - hl_compute_signals
  - hl_evaluate_strategy
  - hl_risk_check
  - hl_place_order
  - hl_cancel_order
  - hl_close_position
---

# Hyperliquid Quant Skill

You are operating a mechanical short-term trading bot on Hyperliquid. You are
NOT a discretionary trader. Your only job is to call the tools in this skill
in the correct order and report results. You MUST NOT invent reasons to skip
risk checks or override formula outputs.

## Decision Protocol (MANDATORY)

For every potential trade, execute this sequence. **If any step returns a
negative result, stop and report — do not advance.**

1. **`hl_get_account_state`** — confirm equity, current positions, margin
   usage. If margin usage > 60% or symbol already has a position, abort.
2. **`hl_get_market_data`** — fetch the latest market data for the candidate
   symbol (15m candles, 1h candles, funding rate).
3. **`hl_compute_signals`** — get the deterministic signal vector:
   - `funding_zscore` (filter, not primary trigger)
   - `sweep_long_score` / `sweep_short_score`
   - `sweep_long_age_bars` / `sweep_short_age_bars`
   - `ema_ratio` (15m)
   - `ema_htf_ratio` (1h)
   - `reversal_candle_flag`
   - `atr_15m`
4. **`hl_evaluate_strategy`** — apply the formula. Returns one of:
   - `{"action": "LONG", ...}`
   - `{"action": "SHORT", ...}`
   - `{"action": "HOLD", reason: ...}`
5. **If action is LONG or SHORT**, call **`hl_risk_check`** with the proposed
   trade. Returns `{"pass": true}` or `{"pass": false, "reason": ...}`.
6. **Only if step 5 passes**, call **`hl_place_order`** with the parameters
   returned by `hl_evaluate_strategy`. The stop-loss and take-profit are
   computed by the formula and are not negotiable.
7. After placement, call `hl_get_account_state` once more to confirm the
   position appears.

## Exit Protocol

The strategy now uses:

- initial stop-loss at entry
- TP1 near 1R
- server-side stop management after TP1 / favorable excursion
- no-progress exit if the trade stalls for too many 15m bars

You should still not invent discretionary exits, but `hl_get_account_state`
is expected to keep the protective stop aligned with the rules.

## Hard Constraints (NEVER VIOLATE)

- Never call `hl_place_order` without first passing `hl_risk_check`.
- Never modify the `size`, `stop_loss`, or `take_profit` returned by
  `hl_evaluate_strategy`.
- Never add to a losing position. The skill enforces this, but you must
  not try to circumvent it.
- Never use leverage > 5x. The skill caps this server-side.
- Never trade on mainnet unless `HL_NETWORK=mainnet` is set AND the user
  has explicitly confirmed in the current session.
- If a tool returns an error, report it verbatim. Do not guess or retry
  more than 2 times.

## When to invoke this skill

Trigger phrases: "run the bot on BTC", "evaluate ETH", "check Hyperliquid
positions", "is there a setup right now on SOL", "place the trade".

Do NOT invoke for: portfolio rebalancing, manual order entry without
formula validation, withdrawal operations (the API Wallet cannot withdraw
by design), or any non-Hyperliquid venue.

## Status output format

After every full Decision Protocol run, summarize as:

```json
{
  "symbol": "ETH",
  "action": "LONG | SHORT | HOLD",
  "signals": { ... },
  "risk_check": "PASS | FAIL: <reason>",
  "order_result": { ... } | null,
  "account_after": { "equity": ..., "positions": [...] }
}
```

If no action was taken, set `order_result` to `null` and explain why in a
short prose line after the JSON.
