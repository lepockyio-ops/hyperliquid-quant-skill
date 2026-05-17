# hyperliquid-quant-skill changes

## 2026-05-16 — Freqtrade repaired V2 artifact upload

Added `artifacts/freqtrade-repaired-v2/` with the current best-performing FreqAI repair candidate snapshot:

- `HLAdaptiveTrendFreqAIRepaired.py`
- `HLAdaptiveTrendFreqAIRepairedV2.py`
- `test_hl_adaptive_trend_freqai_repaired_v2.py`
- README documenting the backtest comparison and why V2 was kept

---

# hyperliquid-quant-skill v0.3 — Critical + Severe fixes

This patch resolves the 5 critical (🟥) and 5 severe (🟧) issues from the audit.

## File map

| File | Purpose |
|---|---|
| `signals.py` | Liquidity-sweep detection replaces fake liquidation cluster |
| `strategy.py` | Sweep-score gating, fee-adjusted sizing, min-notional gate, math.ceil leverage |
| `config.py` | New params: sweep / fees / min_notional / mainnet confirm / decision TTL |
| `execution.py` | Wallet validation, tick/lot rounding, sequential bracket+verify+emergency-close, candle_snapshot fix |
| `risk.py` | `reconcile_pnl_from_fills`, `startup_reconcile`, open_trade tracking |
| `decision_cache.py` | **(new)** Single-use TTL token store — kills LLM forgery vector |
| `mcp_server.py` | Wires all of the above; decision_token flow; auto-close on max_hold; startup reconcile |

## Critical fixes (🟥)

| # | Issue | Resolution |
|---|---|---|
| 1 | Fake liquidation cluster never triggered | `signals.sweep_scores()` — real K-line stop-hunt detection |
| 2 | `bulk_orders` not atomic, naked positions on SL failure | `place_bracket_order` is now sequential, verifies each leg, emergency closes if SL fails |
| 3 | `record_realized_pnl` never called → no daily/consec halt | `reconcile_pnl_from_fills` runs on every `hl_get_account_state` |
| 4 | README claimed wallet check that wasn't implemented | `_verify_api_wallet_is_safe` blocks startup if api_wallet == main OR balance > $100 |
| 5 | SDK schema mismatch + no tick/lot precision | `candle_snapshot` dict signature; size truncated to `szDecimals`; price to 5 sig figs |

## Severe fixes (🟧)

| # | Issue | Resolution |
|---|---|---|
| 6 | LLM could forge `decision_json` | `decision_token` cache; `place_order` only accepts a server-issued token |
| 7 | `override_risk=True` only logged a warning | Hard rejection now |
| 8 | `max_hold_hours` configured but never enforced | `_maybe_enforce_max_hold` auto-closes stale positions in `hl_get_account_state` |
| 9 | Decision could be placed long after signals went stale | Token TTL (default 30s) auto-expires decisions |
| 11 | No min-notional / fee accounting | Sizing now subtracts round-trip taker fees from risk budget; HOLD if notional below `min_notional_usd` |
| 14 | No crash recovery / orphan-order detection | `startup_reconcile` on first `hl_get_account_state` call surfaces drift between state file and exchange |

## New env vars

```bash
# Severe fix #11
HL_TAKER_FEE_BPS=4.5
HL_MIN_NOTIONAL_USD=11

# Severe fixes #6/#9
HL_DECISION_TTL_SECONDS=30

# Critical fix #4 (already in v0.2)
HL_API_WALLET_MAX_BALANCE_USD=100

# Critical fix #2 (already in v0.2)
HL_SL_SLIPPAGE_BPS=50

# Hardware-flag for mainnet (already in v0.2)
HL_MAINNET_CONFIRM=
```

## New tool API

`hl_evaluate_strategy` now returns:
```json
{
  "signals": {...},
  "decision": {...},
  "decision_token": "abc123...",   // single-use, 30s TTL
  "ttl_seconds": 30,
  "equity_usd": 1000.0
}
```

`hl_place_order` now takes:
```json
{
  "decision_token": "abc123..."     // required; the previous decision_json field is gone
}
```

The agent flow:
1. `hl_get_account_state`
2. `hl_evaluate_strategy(symbol)` → returns `decision_token`
3. (optional) `hl_risk_check(decision_token=...)` for diagnostics
4. `hl_place_order(decision_token=...)` — server redeems, runs risk check, executes
5. Token is consumed; same one cannot be replayed

If the agent tries to construct its own decision and send to `hl_place_order`, it will be rejected with `{"status": "rejected", "reason": "decision_token required"}`.

## Smoke tests verified

```
sweep_scores:  long pierce-and-reclaim ✓
sweep_scores:  short pierce-and-reclaim ✓
sweep_scores:  flat market → 0 ✓
DecisionCache: issue/peek/redeem ✓
DecisionCache: single-use (second redeem returns None) ✓
DecisionCache: TTL expiry ✓
DecisionCache: unknown token rejected ✓
strategy.evaluate: LONG with valid signals ✓
strategy.evaluate: HOLD when sweep below threshold ✓
strategy.evaluate: HOLD when notional below min_notional ✓
```

## Pre-mainnet checklist (unchanged)

1. Run `tests/test_strategy.py` and write new sweep / cache / risk tests
2. Mock SDK and test `place_bracket_order` failure modes (SL submission fails → emergency close fires)
3. Testnet: 200 full Decision Protocol runs; manually trigger daily-loss halt and 3-consec-loss cooldown
4. Mainnet: set `HL_MAINNET_CONFIRM=YES_I_UNDERSTAND`, start at $200, review every 50 trades

## Remaining yellow-level issues (next PR)

- Private key still in `.env` (no OS keyring integration)
- Test coverage minimal — only `test_strategy.py` from upstream
- Funding rate annualisation helper (`annualized_basis`) fixed but not yet covered by tests
- No alerting on `position_protected=False` (currently only logs)
