# Execution callback patterns for Freqtrade + Hyperliquid

Use this note when the user wants to keep Freqtrade execution but add more adaptive live order behavior.

## Proven pattern from this session

### Goal
Keep Freqtrade as the execution framework while adding:
- pullback limit entries
- 5s-loop position management
- partial take profit before DCA
- timeout-managed stale orders
- pair-specific leverage caps

### Strategy-side shape

Required class attributes:
```python
position_adjustment_enable = True
max_entry_position_adjustment = 2
```

Common callback split:
- `custom_entry_price()` → initial pullback / passive entry price
- `adjust_trade_position()` → partial TP, DCA, scale-out
- `check_entry_timeout()` / `check_exit_timeout()` → cancel stale orders
- `order_filled()` → persist entry ATR / snapshot data for later logic
- `leverage()` → pair-aware leverage cap

### Important sequencing rule
Inside `adjust_trade_position()`, decide priority explicitly.
A safe default is:
1. ignore if open orders already exist
2. apply partial take-profit first
3. only consider DCA after TP rules do not fire
4. block DCA if factor gating or model-validity checks fail
5. enforce `max_entry_position_adjustment`

This avoids a pathological loop where a fast polling cadence adds size before locking in profits.

## Config-side shape

If using pullback entries, align config with strategy:
```json
"order_time_in_force": {
  "entry": "GTC",
  "exit": "GTC"
},
"custom_price_max_distance_ratio": 0.03,
"unfilledtimeout": {
  "entry": 30,
  "exit": 45,
  "exit_timeout_count": 0,
  "unit": "minutes"
}
```

Interpretation:
- `custom_price_max_distance_ratio` must be wide enough for the pullback price you return.
- strategy timeout callbacks should express the fine-grained policy
- global `unfilledtimeout` should not undercut the custom timeout window

## Cadence model

Keep these separate in reasoning and docs:
- `process_throttle_secs` → bot loop / position-adjust cadence
- `adjust_order_price()` → repricing is candle-bound, not 5s-bound
- `stoploss_on_exchange_interval` → exchange-side stop refresh cadence

Do not promise "everything updates every 5s" just because the bot loop is 5s.

## Minimal test pack

Add focused tests for:
- `custom_entry_price()` returns retracement prices for long and short
- partial TP triggers before DCA
- DCA happens only when signal remains supportive
- factor gate failure blocks DCA
- timeout callbacks cancel stale orders at intended age
- `order_filled()` stores entry metadata
- `leverage()` respects both strategy preference and exchange `max_leverage`

Run these together with existing config-render and factor-gating regression tests.
