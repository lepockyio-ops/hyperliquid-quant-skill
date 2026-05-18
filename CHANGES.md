# hyperliquid-quant-skill changes

## 2026-05-17 — Repository overwritten with current skill bundle

Replaced the previous Hyperliquid MCP skill layout with the current Hyperliquid/Freqtrade quant skill bundle:

- main skill: `software-development/freqtrade-hyperliquid`
- supporting skills: `software-development/quant-strategy-debugging`
  and `software-development/quant-sample-window-and-data-fallbacks`

## 2026-05-18 - V6.2 scored-vote frequency rebalance

Added under the `freqtrade-hyperliquid` skill bundle and the
`quant-strategy-debugging` supporting skill:

- `freqtrade-hyperliquid/references/v6_2-scored-vote-frequency-rebalance.md`:
  durable workflow that switches the per-mode entry surface from a
  multiplicative AND-chain to a must-conditions + scored-vote model, and
  adds hold-time and partial-TP exit guards so V6.1's 1-trade / 10h15m
  backtest can be rebalanced back inside the 30-60 trades/month + 1-5h
  hold target.
- `freqtrade-hyperliquid/references/strategy-templates/HLAdaptiveTrendFreqAI_v6_2.py`:
  concrete IStrategy template (5m main, 1h+4h informative, must + scored
  vote, ATR-1.5x stoploss, partial TP ladder, hold-time guard, post-only
  entries, candle-time freshness check).
- `freqtrade-hyperliquid/references/strategy-templates/config_v6_2_snippets.jsonc`:
  matching runtime config snippets (post-only TIF, stoploss_on_exchange,
  rotated FreqAI identifier, 5m timeframe).
- `additional-skills/quant-strategy-debugging/references/freqai-v6_2-one-trade-vs-target-frequency-case.md`:
  case study documenting the V6.1 1-trade-in-5-days backtest and the
  V6.2 acceptance criteria.

Added core lessons 28-32 to `SKILL.md` (and the mirrored
`freqtrade-hyperliquid/SKILL.md`) covering: N<10 win-rate is meaningless,
AND-chain multiplicative collapse, hold-time guards as the right exit
fix, post-only limit entry economics, and mode-asymmetry persistence
across repair passes.
