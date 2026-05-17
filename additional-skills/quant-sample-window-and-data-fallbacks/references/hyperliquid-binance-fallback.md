# Hyperliquid vs Binance fallback notes for FreqAI frequency validation

## Scenario
A Hyperliquid-perp Freqtrade/FreqAI strategy was being tuned toward a target of **30-60 trades/month**. Initial conclusions were misleading because the usable test window was far shorter than the requested timerange.

## Durable takeaways
- Hyperliquid public 5m OHLCV in this setup only exposed roughly **5000 candles / ~17 days**.
- FreqAI warmup plus `startup_candle_count` can consume most of that history.
- This can leave only a few effective trading days, making monthly-frequency judgments unstable.
- A practical research fallback was to use deeper **Binance futures 5m parquet** for BTC/ETH to validate:
  - signal density
  - entry-funnel losses
  - label-window sensitivity
  - whether the strategy is structurally over-filtered

## Recommended framing
Use the fallback dataset to answer:
- Is the strategy too restrictive?
- Did frequency collapse because of filters, or because of missing usable data?
- Do stop/hold fixes materially change average trade duration and signal realization?

Do **not** use the fallback dataset to claim:
- identical Hyperliquid fill behavior
- identical funding/liq microstructure
- final production readiness on the target venue

## Reporting pattern
When summarizing this class of investigation, report four layers:
1. Requested timerange
2. Actual local data coverage by venue
3. Effective tradable span after warmup/model requirements
4. What conclusions are stable vs provisional

## Example session lesson
Before loosening entry filters to chase 30-60 trades/month, first verify whether the backtest had enough effective sample to express the strategy. In the observed case, the first major bottleneck was not only entry strictness but the fact that the effective Hyperliquid window was too short for trustworthy frequency inference.
