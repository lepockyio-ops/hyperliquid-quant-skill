# Freqtrade Repaired V2 Candidate

This folder snapshots the current best-performing **V2** repair candidate from the Hyperliquid + FreqAI workflow.

## Included files

- `HLAdaptiveTrendFreqAIRepaired.py` — repaired parent strategy that V2 subclasses
- `HLAdaptiveTrendFreqAIRepairedV2.py` — strategy variant with stricter long-side structure filters
- `test_hl_adaptive_trend_freqai_repaired_v2.py` — focused regression tests for the V2 entry logic

## Why V2 was kept

Among the recent repaired variants tested on timerange `20260201-20260501`, V2 was the best result so far:

| Variant | Tot Profit % | Trades | Notes |
|---|---:|---:|---|
| Repaired v5 baseline | -3.94% | 46 | too many same-candle / low-quality long entries |
| **Repaired V2** | **-0.94%** | 10 | strongest improvement; sharply reduced overtrading |
| Repaired V3 | -1.93% | 32 | short-only centered-threshold experiment |
| Repaired V4 | -2.34% | 34 | V3 without retracement entry |

## What changed in V2

V2 keeps the repaired FreqAI shell, then adds long-side structure gating to avoid chasing overheated candles:

- positive candle body required
- candle range / ATR capped
- EMA extension constrained
- retains parent repaired strategy behavior everywhere else

## Notes

- Runtime configs are **not** included here because the local analysis config contained machine-specific values and secrets that should not be committed.
- This snapshot is intended as a code artifact and reference point for the next iteration (`V5` / asymmetric labels or classification).
