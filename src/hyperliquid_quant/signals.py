"""Deterministic signal computations."""

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


def _single_bar_sweep_score(
    highs: list[float],
    lows: list[float],
    opens: list[float],
    closes: list[float],
    sweep_lookback: int,
    idx: int,
) -> tuple[float, float]:
    """Compute weighted sweep scores for one bar index."""
    if idx < sweep_lookback or idx >= len(closes):
        return 0.0, 0.0

    window_start = idx - sweep_lookback
    swing_lo = float(min(lows[window_start:idx]))
    swing_hi = float(max(highs[window_start:idx]))
    last_h = highs[idx]
    last_l = lows[idx]
    last_c = closes[idx]
    last_o = opens[idx]

    bar_range = max(last_h - last_l, 1e-12)

    long_score = 0.0
    if last_l < swing_lo and last_c > swing_lo:
        pierce_depth = swing_lo - last_l
        reclaim = last_c - swing_lo
        pierce_frac = min(1.0, pierce_depth / bar_range)
        reclaim_frac = min(1.0, reclaim / bar_range)
        body_frac = min(1.0, max(0.0, last_c - last_o) / bar_range)
        long_score = 0.45 * pierce_frac + 0.35 * reclaim_frac + 0.20 * body_frac

    short_score = 0.0
    if last_h > swing_hi and last_c < swing_hi:
        pierce_depth = last_h - swing_hi
        reclaim = swing_hi - last_c
        pierce_frac = min(1.0, pierce_depth / bar_range)
        reclaim_frac = min(1.0, reclaim / bar_range)
        body_frac = min(1.0, max(0.0, last_o - last_c) / bar_range)
        short_score = 0.45 * pierce_frac + 0.35 * reclaim_frac + 0.20 * body_frac

    return float(long_score), float(short_score)


def sweep_scores(
    highs: list[float],
    lows: list[float],
    opens: list[float],
    closes: list[float],
    sweep_lookback: int = 20,
    confirmation_bars: int = 2,
) -> tuple[float, float, int, int]:
    """Compute best sweep scores across the most recent confirmation window."""
    if min(len(highs), len(lows), len(opens), len(closes)) < sweep_lookback + 2:
        return 0.0, 0.0, 999, 999

    best_long = 0.0
    best_short = 0.0
    best_long_age = 999
    best_short_age = 999
    last_idx = len(closes) - 1
    max_lookback = min(confirmation_bars, last_idx)

    for age in range(max_lookback):
        idx = last_idx - age
        long_score, short_score = _single_bar_sweep_score(
            highs, lows, opens, closes, sweep_lookback, idx
        )
        if long_score > best_long:
            best_long = long_score
            best_long_age = age
        if short_score > best_short:
            best_short = short_score
            best_short_age = age

    return float(best_long), float(best_short), best_long_age, best_short_age


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

    Captures the higher-timeframe trend and short-term trigger context.
    """

    symbol: str
    current_price: float
    funding_zscore: float
    sweep_long_score: float
    sweep_short_score: float
    sweep_long_age_bars: int
    sweep_short_age_bars: int
    ema_fast: float
    ema_slow: float
    ema_ratio: float
    ema_htf_fast: float
    ema_htf_slow: float
    ema_htf_ratio: float
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
            "sweep_long_age_bars": self.sweep_long_age_bars,
            "sweep_short_age_bars": self.sweep_short_age_bars,
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "ema_ratio": self.ema_ratio,
            "ema_htf_fast": self.ema_htf_fast,
            "ema_htf_slow": self.ema_htf_slow,
            "ema_htf_ratio": self.ema_htf_ratio,
            "atr_15m": self.atr_15m,
            "last_candle_bullish": self.last_candle_bullish,
            "last_candle_bearish": self.last_candle_bearish,
        }
