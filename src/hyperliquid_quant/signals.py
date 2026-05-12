"""Deterministic signal computations.

PATCH NOTES (critical-issue fix #1):
====================================
The original `likely_liquidation_prices` / `nearest_cluster_distance_bps`
used a fabricated "open interest by leverage" derived from L2 orderbook
depth. That proxy was meaningless: the nearest computed cluster was
always a fixed function of current price (e.g. p*(1-1/20) = 500bps away
for the closest 20x bucket), so the default threshold of 10bps was
unreachable and the bot would never trade.

This patched version replaces the liquidation-cluster filter with a
**liquidity-sweep / stop-hunt** filter using only real candle data:

  LONG sweep:  in the last `sweep_lookback` bars the price made a new low
               (below the prior swing low) and then closed BACK ABOVE that
               prior swing low → stops below the swing got hit, then
               price reclaimed → liquidity grab + reversal.
  SHORT sweep: symmetric: new high above prior swing high then close
               back below.

We expose `sweep_long_score` / `sweep_short_score` in [0, 1] where 1
means a textbook sweep happened on the most recent bar. The strategy
then requires `score >= sweep_min_score` (configurable, default 0.6)
in place of the old `near_liq_bps <= threshold` check.

Everything else (funding Z-score, EMA, ATR, reversal flag) is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# =============================================================================
# Funding rate Z-score (unchanged from original)
# =============================================================================


def funding_zscore(funding_history: list[float], window: int = 90) -> float:
    """Z-score of the latest funding rate vs the trailing window.

    Hyperliquid funding settles every hour; ``window=90`` ≈ 90 hours.
    """
    if len(funding_history) < window + 1:
        return 0.0
    arr = np.asarray(funding_history[-(window + 1):], dtype=float)
    sample = arr[:-1]
    latest = arr[-1]
    std = float(np.std(sample))
    if std == 0:
        return 0.0
    return float((latest - float(np.mean(sample))) / std)


def annualized_basis(perp_mid: float, spot_mid: float, funding_hourly: float) -> float:
    """Annualized basis = spot–perp premium plus funding carry.

    PATCH: original used ``funding_8h * 3 * 365`` which was wrong because
    Hyperliquid settles hourly. Fixed to 24 * 365 hourly settlements/yr.

    Returns a unitless fraction (0.02 = 2%/yr).
    """
    if spot_mid <= 0:
        return 0.0
    perp_premium = (perp_mid - spot_mid) / spot_mid
    funding_annual = funding_hourly * 24 * 365
    return perp_premium + funding_annual


# =============================================================================
# Liquidity sweep / stop hunt detection
# (replaces the broken OI-based liquidation cluster signal)
# =============================================================================


@dataclass
class Candle:
    """Local copy to keep this module pure (no cross-import to execution.py)."""
    open: float
    high: float
    low: float
    close: float


def _swing_low(lows: list[float], n_back: int) -> float:
    """Lowest low in the last n_back bars EXCLUDING the most recent bar."""
    if len(lows) < n_back + 1:
        return float("nan")
    return float(min(lows[-n_back - 1:-1]))


def _swing_high(highs: list[float], n_back: int) -> float:
    if len(highs) < n_back + 1:
        return float("nan")
    return float(max(highs[-n_back - 1:-1]))


def sweep_scores(
    highs: list[float],
    lows: list[float],
    opens: list[float],
    closes: list[float],
    sweep_lookback: int = 20,
) -> tuple[float, float]:
    """Compute long-side and short-side sweep scores from candle data.

    A *long sweep* is the classic "stop-hunt reversal" below a swing low:
    - the current bar's LOW pierces the prior swing low (i.e. lows[-1] < swing_low)
    - the current bar's CLOSE is back ABOVE the prior swing low
    - score scales with how deep the wick went AND how far above the level it closed

    Args:
        highs/lows/opens/closes: parallel arrays oldest-first.
        sweep_lookback: how many bars define the "prior swing".

    Returns:
        (long_sweep_score, short_sweep_score) each in [0, 1].
        0 means no sweep, 1 means a strong textbook sweep on the latest bar.
    """
    if min(len(highs), len(lows), len(opens), len(closes)) < sweep_lookback + 2:
        return 0.0, 0.0

    swing_lo = _swing_low(lows, sweep_lookback)
    swing_hi = _swing_high(highs, sweep_lookback)
    last_h = highs[-1]
    last_l = lows[-1]
    last_c = closes[-1]
    last_o = opens[-1]

    # Range of the last bar; used to normalise pierce/reclaim depth
    bar_range = max(last_h - last_l, 1e-12)

    # ---- Long sweep: pierce below swing_lo, close back above ----
    long_score = 0.0
    if last_l < swing_lo and last_c > swing_lo:
        pierce_depth = swing_lo - last_l         # how far below
        reclaim = last_c - swing_lo              # how far back above
        # both components must be meaningful relative to the bar range
        pierce_frac = min(1.0, pierce_depth / bar_range)
        reclaim_frac = min(1.0, reclaim / bar_range)
        # also reward a bullish body
        body_ok = 1.0 if last_c > last_o else 0.3
        long_score = pierce_frac * reclaim_frac * body_ok

    # ---- Short sweep: pierce above swing_hi, close back below ----
    short_score = 0.0
    if last_h > swing_hi and last_c < swing_hi:
        pierce_depth = last_h - swing_hi
        reclaim = swing_hi - last_c
        pierce_frac = min(1.0, pierce_depth / bar_range)
        reclaim_frac = min(1.0, reclaim / bar_range)
        body_ok = 1.0 if last_c < last_o else 0.3
        short_score = pierce_frac * reclaim_frac * body_ok

    return float(long_score), float(short_score)


# =============================================================================
# Technical indicators (used as filters, not primary signals)
# =============================================================================


def ema(values: list[float], period: int) -> float:
    """Exponential moving average; returns latest EMA value."""
    if len(values) < period:
        return float("nan")
    arr = np.asarray(values, dtype=float)
    alpha = 2.0 / (period + 1)
    e = arr[0]
    for v in arr[1:]:
        e = alpha * v + (1 - alpha) * e
    return float(e)


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Average True Range over ``period`` bars. Returns latest value."""
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return float("nan")
    h = np.asarray(highs, dtype=float)
    l_ = np.asarray(lows, dtype=float)
    c = np.asarray(closes, dtype=float)
    prev_close = c[:-1]
    tr = np.maximum.reduce(
        [
            h[1:] - l_[1:],
            np.abs(h[1:] - prev_close),
            np.abs(l_[1:] - prev_close),
        ]
    )
    return float(np.mean(tr[-period:]))


def reversal_candle(open_: float, close: float, side: str) -> bool:
    """Did the last bar reverse in our favor?"""
    if side == "long":
        return close > open_
    if side == "short":
        return close < open_
    return False


# =============================================================================
# Composite signal vector
# =============================================================================


@dataclass
class SignalVector:
    """All signals needed by the strategy formula. Pure data, JSON-serialisable.

    PATCH: replaced ``nearest_long_liq_bps`` / ``nearest_short_liq_bps`` with
    ``sweep_long_score`` / ``sweep_short_score``. Strategy.evaluate() updated
    accordingly.
    """

    symbol: str
    current_price: float
    funding_zscore: float
    sweep_long_score: float
    sweep_short_score: float
    ema_fast: float
    ema_slow: float
    ema_ratio: float
    atr_15m: float
    last_candle_bullish: bool
    last_candle_bearish: bool

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "current_price": self.current_price,
            "funding_zscore": self.funding_zscore,
            "sweep_long_score": self.sweep_long_score,
            "sweep_short_score": self.sweep_short_score,
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "ema_ratio": self.ema_ratio,
            "atr_15m": self.atr_15m,
            "last_candle_bullish": self.last_candle_bullish,
            "last_candle_bearish": self.last_candle_bearish,
        }
