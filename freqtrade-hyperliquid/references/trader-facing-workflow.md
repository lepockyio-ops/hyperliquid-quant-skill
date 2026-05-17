# Trader-facing workflow explanation template

Use this when the user wants the current system explained for an experienced trader who does not write code.

## Recommended framing

Describe the setup as four roles:

1. **Main execution line**
   - Freqtrade is the engine that actually places/cancels orders and manages positions on Hyperliquid.
   - If `dry_run=true`, stress that it is still a simulated execution path, not real capital deployment.

2. **Decision / model layer**
   - FreqAI provides the forecast that helps decide whether a move is worth trading.
   - Explain this as "AI-assisted trade selection" rather than model class names.

3. **External factor layer**
   - Vibe-Trading currently produces parquet factor feeds such as funding, liquidation, and onchain flows.
   - In this workflow those factors are entry filters / vetoes; they help decide whether a setup is tradable.
   - They do **not** place orders and do **not** manage positions unless explicitly wired to do so.

4. **Observation / alert line**
   - A 15-minute Discord script can publish setup summaries such as `【回踩挂多】BTC ...`.
   - Present it as an operator-facing scan / watchlist notification.
   - Do not imply that the 15m alert loop is the same as the live execution timeframe unless the main strategy was actually moved to 15m.

## Cadence wording

Always distinguish these three clocks:

- **Signal timeframe:** the candle level used to generate entries/exits (for example 1h, with 4h direction confirmation).
- **Management cadence:** how often the bot re-checks open orders / position adjustments (for example every 5 seconds via `process_throttle_secs`).
- **Alert cadence:** how often a notification script posts a fresh market scan (for example every 15 minutes to Discord).

This avoids the common misunderstanding: "I asked for a 15m scan, so the bot must now be trading 15m."

## Example summary structure

- Main bot: 1h short/mid-term trend trader, 4h direction filter, Hyperliquid executor.
- Risk: single open trade, leverage cap 5x, per-trade stake cap 100 USDC margin, exchange-side stoploss enabled.
- AI role: helps judge whether the expected move is strong enough.
- Vibe role: provides funding/liquidation/onchain context; if stale or missing, the bot can block entries.
- Discord 15m role: observation and discretionary setup broadcast, not the execution engine itself.
