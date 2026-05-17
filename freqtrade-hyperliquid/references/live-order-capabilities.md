# Live order capabilities: Freqtrade + Hyperliquid

## Session-tested conclusions

### Runtime config rendering
Use the custom renderer with explicit arguments:

```bash
python scripts/render_freqtrade_config.py \
  --env .env \
  --template user_data/config.freqai.hyperliquid.template.json \
  --output user_data/config.runtime.freqai.json
```

Expected verification:
- stdout prints `WROTE user_data/config.runtime.freqai.json`
- rendered JSON has `freqai.enabled = true`
- rendered JSON has `exchange.name = "hyperliquid"`

If the project uses a custom renderer, add/verify a test that proves the script honors `--env`, `--template`, and `--output` instead of silently writing a hardcoded file.

### Parent/child or bracket behavior
For Hyperliquid futures under Freqtrade:
- `stoploss_on_exchange` is supported.
- Freqtrade's Hyperliquid exchange adapter advertises futures `stoploss_on_exchange = True`.
- Hyperliquid stoploss support here is exchange-side **stop-loss-limit**.
- This is **not** equivalent to a full native bracket/OCO parent-child order set with entry + TP + SL all atomically linked.

Operationally, think of it as:
1. place entry
2. wait for fill
3. Freqtrade places and later maintains exchange-side stoploss order

### Dynamic position adjustment
Use `position_adjustment_enable = True` and `adjust_trade_position()` for:
- DCA / scale-in
- partial exits
- profit-taking reductions
- risk-based size changes on an open trade

Important limits from docs/behavior:
- live/dry-run adjustment callback cadence follows `internals.process_throttle_secs` (often 5s)
- additional orders do not count toward `max_open_trades`
- adjustments can cancel/replace existing open orders if amount/price/direction changes
- leverage cannot be modified through `adjust_trade_position()`

### Dynamic stoploss adjustment
Use `custom_stoploss()` for strategy-side stop updates.

But distinguish:
- strategy may compute stop intentions frequently
- exchange-side stoploss refresh cadence follows `order_types.stoploss_on_exchange_interval` (often 60s)

So exchange-side stop maintenance is slower than the main trade loop by design.

### Pullback / resting limit entry workflows
Freqtrade can do more than "signal appears, immediately cross the market".

Useful callbacks/config for resting orders:
- `custom_entry_price()`
- `adjust_order_price()`
- `check_entry_timeout()`
- `unfilledtimeout`
- limit `entry` order type

This supports workflows like:
- signal now, bid lower on pullback
- leave order resting for N minutes
- cancel if not filled
- replace with a new price if conditions change

### Mental model to preserve
There are three separate cadences:
1. **bot loop cadence** via `process_throttle_secs`
2. **exchange stoploss refresh cadence** via `stoploss_on_exchange_interval`
3. **signal cadence** via candle timeframe / `process_only_new_candles`

Do not collapse these into one concept when answering user questions about what can be adjusted "every poll".
