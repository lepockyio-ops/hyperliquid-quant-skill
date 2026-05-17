"""The deterministic strategy formula."""

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

    Long  triggers when:
        - 1h EMA fast > EMA slow by ema_trend_min
        - 15m EMA fast >= EMA slow
        - recent long sweep score >= sweep_min_score
        - funding is not too crowded on the long side
        - latest candle confirms upward follow-through

    Short triggers when the symmetric short conditions hold.

    Otherwise HOLD.
    """
    fz = signals.funding_zscore
    p = signals.current_price
    if p <= 0:
        return TradeDecision(action="HOLD", symbol=signals.symbol, reason="invalid price")

    trend_15m = signals.ema_ratio - 1.0
    trend_1h = signals.ema_htf_ratio - 1.0

    long_funding_ok = fz <= config.funding_z_threshold
    long_sweep_ok = signals.sweep_long_score >= config.sweep_min_score
    long_trigger_trend_ok = trend_15m >= 0
    long_trend_ok = trend_1h > config.ema_trend_min
    long_reversal_ok = signals.last_candle_bullish
    long_sweep_fresh = signals.sweep_long_age_bars < config.sweep_confirmation_bars

    if (
        long_funding_ok
        and long_sweep_ok
        and long_trigger_trend_ok
        and long_trend_ok
        and long_reversal_ok
        and long_sweep_fresh
    ):
        return _build_decision(
            side="LONG", signals=signals, equity_usd=equity_usd, config=config
        )

    short_funding_ok = fz >= -config.funding_z_threshold
    short_sweep_ok = signals.sweep_short_score >= config.sweep_min_score
    short_trigger_trend_ok = trend_15m <= 0
    short_trend_ok = trend_1h < -config.ema_trend_min
    short_reversal_ok = signals.last_candle_bearish
    short_sweep_fresh = signals.sweep_short_age_bars < config.sweep_confirmation_bars

    if (
        short_funding_ok
        and short_sweep_ok
        and short_trigger_trend_ok
        and short_trend_ok
        and short_reversal_ok
        and short_sweep_fresh
    ):
        return _build_decision(
            side="SHORT", signals=signals, equity_usd=equity_usd, config=config
        )

    reasons = []
    if not long_funding_ok and not short_funding_ok:
        reasons.append(f"funding_z={fz:+.2f} outside filter window")
    if not (long_sweep_ok or short_sweep_ok):
        reasons.append(
            f"no sweep (long={signals.sweep_long_score:.2f}, "
            f"short={signals.sweep_short_score:.2f}, "
            f"need >={config.sweep_min_score})"
        )
    if not (long_sweep_fresh or short_sweep_fresh):
        reasons.append(
            f"sweep too old (long_age={signals.sweep_long_age_bars}, short_age={signals.sweep_short_age_bars})"
        )
    if not (long_trend_ok or short_trend_ok):
        reasons.append(
            f"1h trend={trend_1h:+.4f} within ±{config.ema_trend_min}"
        )
    if not (long_trigger_trend_ok or short_trigger_trend_ok):
        reasons.append(f"15m trigger trend={trend_15m:+.4f} misaligned")
    if not (long_reversal_ok or short_reversal_ok):
        reasons.append("last candle not a reversal in any direction")
    if long_funding_ok and not short_funding_ok and not (long_sweep_ok and long_trend_ok):
        reasons.append("short side blocked by crowded negative funding")
    if short_funding_ok and not long_funding_ok and not (short_sweep_ok and short_trend_ok):
        reasons.append("long side blocked by crowded positive funding")

    return TradeDecision(
        action="HOLD",
        symbol=signals.symbol,
        reason="; ".join(reasons) if reasons else "no edge",
    )


def _breakeven_price(
    *,
    side: Literal["LONG", "SHORT"],
    price: float,
    config: Config,
) -> float:
    buffer = config.breakeven_buffer_bps / 10000.0
    fee_buffer = 2.0 * (config.taker_fee_bps / 10000.0)
    if side == "LONG":
        return price * (1.0 + buffer + fee_buffer)
    return price * (1.0 - buffer - fee_buffer)


def _trail_anchor_price(
    *,
    side: Literal["LONG", "SHORT"],
    entry: float,
    sl_dist: float,
    config: Config,
) -> float:
    """Optional second target used as a hard cap if the trailing layer is inactive."""
    if side == "LONG":
        return entry + sl_dist * max(config.tp1_rr + 0.8, 1.8)
    return entry - sl_dist * max(config.tp1_rr + 0.8, 1.8)


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

    sl_dist = max(atr_val * config.sl_atr_mult, p * config.sl_min_frac)
    risk_amount = equity_usd * config.risk_per_trade

    # PATCH (severe fix #11): account for round-trip taker fees in sizing.
    # Worst-case loss = sl_dist + (entry_fee + exit_fee) per unit position.
    # Per-unit fee cost ≈ 2 × (taker_fee_bps / 10000) × p.
    fee_cost_per_unit = 2.0 * (config.taker_fee_bps / 10000.0) * p
    effective_sl_dist = sl_dist + fee_cost_per_unit
    raw_notional = (risk_amount / effective_sl_dist) * p

    max_notional_by_frac = equity_usd * config.max_position_frac
    notional = min(raw_notional, max_notional_by_frac)

    max_notional_by_lev = equity_usd * config.max_leverage
    notional = min(notional, max_notional_by_lev)

    if notional <= 0:
        return TradeDecision(
            action="HOLD", symbol=signals.symbol, reason="notional <= 0 after caps"
        )

    # PATCH (severe fix #11): venue min notional check.
    if notional < config.min_notional_usd:
        return TradeDecision(
            action="HOLD",
            symbol=signals.symbol,
            reason=(
                f"notional ${notional:.2f} below venue minimum "
                f"${config.min_notional_usd:.2f}; account too small for this "
                f"risk_per_trade × sl_dist combination"
            ),
        )

    size = notional / p

    if side == "LONG":
        stop_loss = p - sl_dist
        take_profit_1 = p + sl_dist * config.tp1_rr
        take_profit_2 = _trail_anchor_price(side=side, entry=p, sl_dist=sl_dist, config=config)
    else:
        stop_loss = p + sl_dist
        take_profit_1 = p - sl_dist * config.tp1_rr
        take_profit_2 = _trail_anchor_price(side=side, entry=p, sl_dist=sl_dist, config=config)

    import math
    used_leverage = max(1, min(config.max_leverage, math.ceil(notional / equity_usd)))

    score = (
        signals.sweep_long_score if side == "LONG" else signals.sweep_short_score
    )
    return TradeDecision(
        action=side,
        symbol=signals.symbol,
        reason=(
            f"{side} setup: F_z={signals.funding_zscore:+.2f}, "
            f"sweep_score={score:.2f}, 1h EMA ratio {signals.ema_htf_ratio:.4f}, "
            f"15m EMA ratio {signals.ema_ratio:.4f}, "
            f"tp1 at 1R then protect via breakeven/trailing"
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
