"""The deterministic strategy formula.

Pure function: market data + config → trade decision. No I/O, no LLM input.
This is the heart of the bot — every entry must come through `evaluate()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import Config
from .signals import SignalVector

Action = Literal["LONG", "SHORT", "HOLD"]


@dataclass
class TradeDecision:
    """Output of strategy.evaluate(). All numeric fields are in quote units (USD)
    except size which is in base units (coin)."""

    action: Action
    symbol: str
    reason: str
    entry_price: float = 0.0
    size: float = 0.0
    notional: float = 0.0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    leverage: int = 1
    risk_amount: float = 0.0
    sl_distance: float = 0.0

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "symbol": self.symbol,
            "reason": self.reason,
            "entry_price": self.entry_price,
            "size": self.size,
            "notional": self.notional,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "leverage": self.leverage,
            "risk_amount": self.risk_amount,
            "sl_distance": self.sl_distance,
        }


def evaluate(
    signals: SignalVector,
    equity_usd: float,
    config: Config,
) -> TradeDecision:
    """Apply the entry formula.

    Long  triggers when:  F_z < -threshold
                          AND nearest_long_liq within liq_proximity_bps
                          AND ema_ratio > 0.998
                          AND last_candle_bullish

    Short triggers when:  F_z > +threshold
                          AND nearest_short_liq within liq_proximity_bps
                          AND ema_ratio < 1.002
                          AND last_candle_bearish

    Otherwise HOLD.
    """
    fz = signals.funding_zscore
    p = signals.current_price
    if p <= 0:
        return TradeDecision(action="HOLD", symbol=signals.symbol, reason="invalid price")

    # ---- Long branch ----------------------------------------------------
    long_funding_ok = fz < -config.funding_z_threshold
    long_liq_near = signals.nearest_long_liq_bps <= config.liq_proximity_bps
    long_trend_ok = signals.ema_ratio > 0.998
    long_reversal_ok = signals.last_candle_bullish

    if long_funding_ok and long_liq_near and long_trend_ok and long_reversal_ok:
        return _build_decision(
            side="LONG", signals=signals, equity_usd=equity_usd, config=config
        )

    # ---- Short branch ---------------------------------------------------
    short_funding_ok = fz > config.funding_z_threshold
    short_liq_near = signals.nearest_short_liq_bps <= config.liq_proximity_bps
    short_trend_ok = signals.ema_ratio < 1.002
    short_reversal_ok = signals.last_candle_bearish

    if short_funding_ok and short_liq_near and short_trend_ok and short_reversal_ok:
        return _build_decision(
            side="SHORT", signals=signals, equity_usd=equity_usd, config=config
        )

    # ---- No setup -------------------------------------------------------
    reasons = []
    if not (long_funding_ok or short_funding_ok):
        reasons.append(
            f"funding_z={fz:+.2f} within ±{config.funding_z_threshold}"
        )
    if not (long_liq_near or short_liq_near):
        reasons.append(
            f"no liq cluster within {config.liq_proximity_bps}bps "
            f"(long={signals.nearest_long_liq_bps:.1f}bps, "
            f"short={signals.nearest_short_liq_bps:.1f}bps)"
        )
    if not (long_trend_ok or short_trend_ok):
        reasons.append(f"ema_ratio={signals.ema_ratio:.4f} ambiguous trend")
    if not (long_reversal_ok or short_reversal_ok):
        reasons.append("last candle not a reversal in any direction")

    return TradeDecision(
        action="HOLD",
        symbol=signals.symbol,
        reason="; ".join(reasons) if reasons else "no edge",
    )


# =============================================================================
# Position sizing (deterministic)
# =============================================================================


def _build_decision(
    *,
    side: Literal["LONG", "SHORT"],
    signals: SignalVector,
    equity_usd: float,
    config: Config,
) -> TradeDecision:
    p = signals.current_price
    atr_val = signals.atr_15m
    if atr_val != atr_val or atr_val <= 0:  # NaN check
        return TradeDecision(
            action="HOLD",
            symbol=signals.symbol,
            reason="ATR unavailable, cannot size position",
        )

    # Stop-loss distance: max(ATR × multiplier, floor as fraction of price)
    sl_dist = max(atr_val * config.sl_atr_mult, p * config.sl_min_frac)

    # Risk amount
    risk_amount = equity_usd * config.risk_per_trade

    # Notional sized so that hitting SL loses risk_amount
    raw_notional = (risk_amount / sl_dist) * p

    # Cap notional at max_position_frac × equity (without leverage scaling)
    max_notional_by_frac = equity_usd * config.max_position_frac
    notional = min(raw_notional, max_notional_by_frac)

    # Cap by max leverage: notional cannot exceed equity × max_leverage
    max_notional_by_lev = equity_usd * config.max_leverage
    notional = min(notional, max_notional_by_lev)

    if notional <= 0:
        return TradeDecision(
            action="HOLD", symbol=signals.symbol, reason="notional <= 0 after caps"
        )

    size = notional / p  # base units

    if side == "LONG":
        stop_loss = p - sl_dist
        take_profit_1 = p + sl_dist * config.tp1_rr
        take_profit_2 = p + sl_dist * config.tp1_rr * 2.0
    else:  # SHORT
        stop_loss = p + sl_dist
        take_profit_1 = p - sl_dist * config.tp1_rr
        take_profit_2 = p - sl_dist * config.tp1_rr * 2.0

    # Effective leverage = notional / margin used. For a 5x cap, the margin is
    # notional / 5. We report the integer leverage the bot will set.
    used_leverage = max(1, min(config.max_leverage, int(notional / equity_usd) + 1))

    return TradeDecision(
        action=side,
        symbol=signals.symbol,
        reason=(
            f"{side} setup: F_z={signals.funding_zscore:+.2f}, "
            f"liq cluster {min(signals.nearest_long_liq_bps, signals.nearest_short_liq_bps):.1f}bps, "
            f"EMA ratio {signals.ema_ratio:.4f}, reversal bar confirmed"
        ),
        entry_price=p,
        size=size,
        notional=notional,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        leverage=used_leverage,
        risk_amount=risk_amount,
        sl_distance=sl_dist,
    )
