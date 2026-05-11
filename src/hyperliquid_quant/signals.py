"""Deterministic signal computations.

Ports the core logic of Vibe-Trading's `perp-funding-basis` and
`liquidation-heatmap` skills to standalone numpy. All functions are pure —
input data in, scalar/structured signals out. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# =============================================================================
# Funding rate Z-score (perp-funding-basis)
# =============================================================================


def funding_zscore(funding_history: list[float], window: int = 90) -> float:
    """Z-score of the latest funding rate vs the trailing window.

    Hyperliquid funding settles every hour; ``window=90`` ≈ 90 hours ≈ 3.75
    days. For the conservative 30-day version used by some strategies, pass
    ``window=720``. The current strategy uses ``window=90`` because Hyperliquid
    funding is hourly and a long window dilutes the signal.

    Args:
        funding_history: list of recent funding rates, oldest first.
        window: number of trailing samples to compute mean/std over.

    Returns:
        z-score of the most recent funding rate. 0 if window has zero variance.
    """
    if len(funding_history) < window + 1:
        # Not enough history — be safe, return 0 so strategy will HOLD.
        return 0.0
    arr = np.asarray(funding_history[-(window + 1) :], dtype=float)
    sample = arr[:-1]
    latest = arr[-1]
    std = float(np.std(sample))
    if std == 0:
        return 0.0
    return float((latest - float(np.mean(sample))) / std)


def annualized_basis(perp_mid: float, spot_mid: float, funding_8h: float) -> float:
    """Annualized basis = spot–perp premium plus funding carry.

    Returns a unitless fraction (0.02 = 2%/yr).
    """
    if spot_mid <= 0:
        return 0.0
    perp_premium = (perp_mid - spot_mid) / spot_mid
    funding_annual = funding_8h * 3 * 365  # 3 settlements per day
    return perp_premium + funding_annual


# =============================================================================
# Liquidation heatmap (liquidation-heatmap)
# =============================================================================


@dataclass
class LiqCluster:
    """A region of likely-liquidation prices."""

    price: float
    side: str  # "long_liq" (longs get liquidated below) or "short_liq"
    intensity: float  # heuristic, higher = more open interest concentrated


def likely_liquidation_prices(
    open_interest_by_leverage: dict[int, float],
    current_price: float,
    side: str,
) -> list[LiqCluster]:
    """Estimate likely liquidation price clusters.

    Liquidation price for an isolated position at leverage L (ignoring fees /
    funding) is roughly::

        long :  P_liq = entry × (1 - 1/L)
        short:  P_liq = entry × (1 + 1/L)

    For a cross-margin or maintenance-margin account the actual liquidation
    is lower, but this approximation locates the clusters well enough for
    short-term decision-making.

    Args:
        open_interest_by_leverage: {leverage_bucket: notional_open_interest}.
            E.g., ``{3: 1_200_000, 5: 4_500_000, 10: 2_100_000}``.
        current_price: current mark price; used as the proxy entry for the
            recently-opened OI in each bucket.
        side: "long" → return clusters where longs would be liquidated (below
            price). "short" → where shorts would be liquidated (above price).

    Returns:
        list of LiqCluster sorted by intensity descending.
    """
    if current_price <= 0:
        return []
    clusters: list[LiqCluster] = []
    for lev, notional in open_interest_by_leverage.items():
        if lev <= 1 or notional <= 0:
            continue
        if side == "long":
            price = current_price * (1.0 - 1.0 / lev)
            clusters.append(LiqCluster(price=price, side="long_liq", intensity=notional))
        elif side == "short":
            price = current_price * (1.0 + 1.0 / lev)
            clusters.append(LiqCluster(price=price, side="short_liq", intensity=notional))
    clusters.sort(key=lambda c: c.intensity, reverse=True)
    return clusters


def nearest_cluster_distance_bps(
    clusters: list[LiqCluster],
    current_price: float,
) -> float:
    """Distance in basis points from current price to the nearest cluster.

    Returns ``inf`` if there are no clusters.
    """
    if not clusters or current_price <= 0:
        return float("inf")
    distances = [abs(c.price - current_price) / current_price * 10000 for c in clusters]
    return float(min(distances))


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
    """Did the last bar reverse in our favor?

    side='long'  → close > open (bullish bar)
    side='short' → close < open (bearish bar)
    """
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
    """All signals needed by the strategy formula. Pure data, JSON-serialisable."""

    symbol: str
    current_price: float
    funding_zscore: float
    nearest_long_liq_bps: float
    nearest_short_liq_bps: float
    ema_fast: float
    ema_slow: float
    ema_ratio: float  # ema_fast / ema_slow
    atr_15m: float
    last_candle_bullish: bool
    last_candle_bearish: bool

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "current_price": self.current_price,
            "funding_zscore": self.funding_zscore,
            "nearest_long_liq_bps": self.nearest_long_liq_bps,
            "nearest_short_liq_bps": self.nearest_short_liq_bps,
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "ema_ratio": self.ema_ratio,
            "atr_15m": self.atr_15m,
            "last_candle_bullish": self.last_candle_bullish,
            "last_candle_bearish": self.last_candle_bearish,
        }
