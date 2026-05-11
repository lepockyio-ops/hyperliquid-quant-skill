"""Smoke tests for the pure-function layers. No network, no SDK required."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hyperliquid_quant.config import Config
from hyperliquid_quant.risk import AccountSnapshot, RiskCheckResult, StateStore, check_trade
from hyperliquid_quant.signals import (
    SignalVector,
    atr,
    ema,
    funding_zscore,
    likely_liquidation_prices,
    nearest_cluster_distance_bps,
    reversal_candle,
)
from hyperliquid_quant.strategy import evaluate


def _cfg(**overrides) -> Config:
    base = Config()
    return replace(base, **overrides) if overrides else base


# =============================================================================
# Signal tests
# =============================================================================


def test_funding_zscore_zero_when_no_history():
    assert funding_zscore([], window=30) == 0.0
    assert funding_zscore([0.0001] * 5, window=30) == 0.0


def test_funding_zscore_detects_extreme_negative():
    base = [0.0001] * 90
    series = base + [-0.005]  # massive negative funding
    z = funding_zscore(series, window=90)
    assert z < -3, f"expected strong negative z, got {z}"


def test_ema_returns_finite():
    closes = list(range(100))
    assert ema(closes, 9) > 0
    assert ema(closes, 21) > 0


def test_atr_returns_finite_for_simple_series():
    n = 30
    highs = [100 + i for i in range(n)]
    lows = [99 + i for i in range(n)]
    closes = [99.5 + i for i in range(n)]
    a = atr(highs, lows, closes, period=14)
    assert a > 0


def test_reversal_candle():
    assert reversal_candle(100, 101, "long") is True
    assert reversal_candle(100, 99, "long") is False
    assert reversal_candle(100, 99, "short") is True
    assert reversal_candle(100, 101, "short") is False


def test_liq_clusters_long_below_short_above():
    price = 1000.0
    oi = {3: 100, 5: 200, 10: 300}
    longs = likely_liquidation_prices(oi, price, "long")
    shorts = likely_liquidation_prices(oi, price, "short")
    assert all(c.price < price for c in longs)
    assert all(c.price > price for c in shorts)


def test_cluster_distance_bps():
    from hyperliquid_quant.signals import LiqCluster

    clusters = [LiqCluster(price=1010, side="short_liq", intensity=1)]
    d = nearest_cluster_distance_bps(clusters, 1000.0)
    assert abs(d - 100.0) < 0.01


# =============================================================================
# Strategy tests
# =============================================================================


def _build_signals(**kw) -> SignalVector:
    defaults = dict(
        symbol="ETH",
        current_price=2000.0,
        funding_zscore=0.0,
        nearest_long_liq_bps=999.0,
        nearest_short_liq_bps=999.0,
        ema_fast=2000.0,
        ema_slow=2000.0,
        ema_ratio=1.0,
        atr_15m=5.0,
        last_candle_bullish=False,
        last_candle_bearish=False,
    )
    defaults.update(kw)
    return SignalVector(**defaults)


def test_hold_when_no_edge():
    sig = _build_signals()
    d = evaluate(sig, equity_usd=10_000, config=_cfg())
    assert d.action == "HOLD"


def test_long_when_all_conditions_met():
    sig = _build_signals(
        funding_zscore=-2.5,
        nearest_long_liq_bps=5.0,
        ema_ratio=1.0,
        last_candle_bullish=True,
    )
    d = evaluate(sig, equity_usd=10_000, config=_cfg())
    assert d.action == "LONG"
    assert d.size > 0
    assert d.stop_loss < d.entry_price
    assert d.take_profit_1 > d.entry_price


def test_short_when_all_conditions_met():
    sig = _build_signals(
        funding_zscore=2.5,
        nearest_short_liq_bps=5.0,
        ema_ratio=1.0,
        last_candle_bearish=True,
    )
    d = evaluate(sig, equity_usd=10_000, config=_cfg())
    assert d.action == "SHORT"
    assert d.stop_loss > d.entry_price
    assert d.take_profit_1 < d.entry_price


def test_position_sizing_respects_risk_per_trade():
    sig = _build_signals(
        funding_zscore=-2.5,
        nearest_long_liq_bps=5.0,
        ema_ratio=1.0,
        last_candle_bullish=True,
    )
    equity = 10_000.0
    cfg = _cfg()
    d = evaluate(sig, equity_usd=equity, config=cfg)
    # Risk = (entry - SL) × size ≈ risk_per_trade × equity, within sizing caps
    risk_dollar = (d.entry_price - d.stop_loss) * d.size
    expected = equity * cfg.risk_per_trade
    # Should be ≤ expected (caps may reduce, never exceed)
    assert risk_dollar <= expected * 1.01


def test_position_capped_by_max_position_frac():
    sig = _build_signals(
        funding_zscore=-2.5,
        nearest_long_liq_bps=5.0,
        ema_ratio=1.0,
        last_candle_bullish=True,
        atr_15m=0.01,  # tiny ATR → would otherwise size huge
    )
    equity = 10_000.0
    cfg = _cfg()
    d = evaluate(sig, equity_usd=equity, config=cfg)
    assert d.notional <= equity * cfg.max_position_frac * 1.01


# =============================================================================
# Risk-guard tests
# =============================================================================


@pytest.fixture
def tmp_state(tmp_path) -> StateStore:
    return StateStore(tmp_path / "state.json")


def _winning_long_decision():
    from hyperliquid_quant.strategy import TradeDecision

    return TradeDecision(
        action="LONG",
        symbol="ETH",
        reason="test",
        entry_price=2000,
        size=0.5,
        notional=1000,
        stop_loss=1990,
        take_profit_1=2015,
        take_profit_2=2030,
        leverage=2,
        risk_amount=50,
        sl_distance=10,
    )


def test_risk_pass_clean(tmp_state):
    d = _winning_long_decision()
    acct = AccountSnapshot(equity_usd=10_000, margin_used_usd=0, positions=[])
    r = check_trade(d, acct, tmp_state, _cfg())
    assert isinstance(r, RiskCheckResult)
    assert r.passed, r.reasons


def test_risk_reject_existing_position(tmp_state):
    d = _winning_long_decision()
    acct = AccountSnapshot(
        equity_usd=10_000,
        margin_used_usd=0,
        positions=[{"coin": "ETH", "size": 1.0, "entry_px": 1980}],
    )
    r = check_trade(d, acct, tmp_state, _cfg())
    assert not r.passed
    assert any("already have a position" in x for x in r.reasons)


def test_risk_reject_high_margin(tmp_state):
    d = _winning_long_decision()
    acct = AccountSnapshot(equity_usd=10_000, margin_used_usd=8_000, positions=[])
    r = check_trade(d, acct, tmp_state, _cfg())
    assert not r.passed
    assert any("margin usage" in x for x in r.reasons)


def test_risk_reject_missing_stop_loss(tmp_state):
    d = _winning_long_decision()
    d.stop_loss = 0
    acct = AccountSnapshot(equity_usd=10_000, margin_used_usd=0, positions=[])
    r = check_trade(d, acct, tmp_state, _cfg())
    assert not r.passed
    assert any("stop_loss" in x for x in r.reasons)


def test_risk_reject_wrong_side_sl(tmp_state):
    d = _winning_long_decision()
    d.stop_loss = d.entry_price + 10  # wrong side for long
    acct = AccountSnapshot(equity_usd=10_000, margin_used_usd=0, positions=[])
    r = check_trade(d, acct, tmp_state, _cfg())
    assert not r.passed
    assert any("not below entry" in x for x in r.reasons)
