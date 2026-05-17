"""Environment-driven config. Pure data, no side effects beyond reading os.environ.

PATCH NOTES (critical + severe fixes):
======================================
- Removed `liq_proximity_bps`. Added `sweep_min_score` and `ema_trend_min`.
- Added `mainnet_confirm_token` — operator must set HL_MAINNET_CONFIRM=YES_I_UNDERSTAND
  to actually trade on mainnet.
- Added `api_wallet_max_balance_usd` for runtime API-wallet sanity check.
- Added `taker_fee_bps` so position sizing accounts for round-trip fees.
- Added `min_notional_usd` so decisions below venue minimum are HOLD'd.
- Added `decision_ttl_seconds` for the decision_token cache TTL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_list(key: str, default: list[str]) -> list[str]:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return list(default)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


@dataclass(frozen=True)
class Config:
    # ---- Network ----
    network: str = field(default_factory=lambda: os.environ.get("HL_NETWORK", "testnet"))
    api_wallet_private_key: str = field(
        default_factory=lambda: os.environ.get("HL_API_WALLET_PRIVATE_KEY", "")
    )
    main_address: str = field(default_factory=lambda: os.environ.get("HL_MAIN_ADDRESS", ""))
    mainnet_confirm: str = field(
        default_factory=lambda: os.environ.get("HL_MAINNET_CONFIRM", "")
    )

    # ---- Risk ----
    risk_per_trade: float = field(default_factory=lambda: _env_float("HL_RISK_PER_TRADE", 0.005))
    max_position_frac: float = field(
        default_factory=lambda: _env_float("HL_MAX_POSITION_FRAC", 0.20)
    )
    max_leverage: int = field(default_factory=lambda: _env_int("HL_MAX_LEVERAGE", 5))
    daily_loss_limit: float = field(
        default_factory=lambda: _env_float("HL_DAILY_LOSS_LIMIT", 0.02)
    )
    consec_loss_limit: int = field(default_factory=lambda: _env_int("HL_CONSEC_LOSS_LIMIT", 3))
    cooldown_hours: int = field(default_factory=lambda: _env_int("HL_COOLDOWN_HOURS", 4))
    max_margin_usage: float = field(
        default_factory=lambda: _env_float("HL_MAX_MARGIN_USAGE", 0.60)
    )
    max_hold_hours: int = field(default_factory=lambda: _env_int("HL_MAX_HOLD_HOURS", 4))
    sl_slippage_bps: float = field(
        default_factory=lambda: _env_float("HL_SL_SLIPPAGE_BPS", 50)
    )
    api_wallet_max_balance_usd: float = field(
        default_factory=lambda: _env_float("HL_API_WALLET_MAX_BALANCE_USD", 100.0)
    )
    # Severe fix #11: round-trip taker fees.
    taker_fee_bps: float = field(default_factory=lambda: _env_float("HL_TAKER_FEE_BPS", 4.5))
    # Severe fix #11: venue minimum notional.
    min_notional_usd: float = field(default_factory=lambda: _env_float("HL_MIN_NOTIONAL_USD", 11.0))
    # Severe fixes #6/#9: decision cache TTL.
    decision_ttl_seconds: int = field(
        default_factory=lambda: _env_int("HL_DECISION_TTL_SECONDS", 30)
    )

    # ---- Strategy ----
    funding_z_threshold: float = field(
        default_factory=lambda: _env_float("HL_FUNDING_Z_THRESHOLD", 1.1)
    )
    sweep_min_score: float = field(
        default_factory=lambda: _env_float("HL_SWEEP_MIN_SCORE", 0.55)
    )
    sweep_lookback: int = field(default_factory=lambda: _env_int("HL_SWEEP_LOOKBACK", 12))
    sweep_confirmation_bars: int = field(
        default_factory=lambda: _env_int("HL_SWEEP_CONFIRMATION_BARS", 2)
    )
    ema_trend_min: float = field(
        default_factory=lambda: _env_float("HL_EMA_TREND_MIN", 0.0005)
    )
    trigger_ema_fast_period: int = field(
        default_factory=lambda: _env_int("HL_TRIGGER_EMA_FAST_PERIOD", 9)
    )
    trigger_ema_slow_period: int = field(
        default_factory=lambda: _env_int("HL_TRIGGER_EMA_SLOW_PERIOD", 21)
    )
    higher_tf_ema_fast_period: int = field(
        default_factory=lambda: _env_int("HL_HIGHER_TF_EMA_FAST_PERIOD", 20)
    )
    higher_tf_ema_slow_period: int = field(
        default_factory=lambda: _env_int("HL_HIGHER_TF_EMA_SLOW_PERIOD", 50)
    )
    sl_atr_mult: float = field(default_factory=lambda: _env_float("HL_SL_ATR_MULT", 1.0))
    sl_min_frac: float = field(default_factory=lambda: _env_float("HL_SL_MIN_FRAC", 0.003))
    tp1_rr: float = field(default_factory=lambda: _env_float("HL_TP1_RR", 1.0))
    trail_activation_rr: float = field(
        default_factory=lambda: _env_float("HL_TRAIL_ACTIVATION_RR", 1.0)
    )
    trail_atr_mult: float = field(
        default_factory=lambda: _env_float("HL_TRAIL_ATR_MULT", 1.3)
    )
    trail_ema_period: int = field(
        default_factory=lambda: _env_int("HL_TRAIL_EMA_PERIOD", 9)
    )
    no_progress_bars: int = field(
        default_factory=lambda: _env_int("HL_NO_PROGRESS_BARS", 4)
    )
    no_progress_min_rr: float = field(
        default_factory=lambda: _env_float("HL_NO_PROGRESS_MIN_RR", 0.35)
    )
    breakeven_buffer_bps: float = field(
        default_factory=lambda: _env_float("HL_BREAKEVEN_BUFFER_BPS", 5.0)
    )
    universe: list[str] = field(
        default_factory=lambda: _env_list("HL_UNIVERSE", ["BTC", "ETH"])
    )

    # ---- Operational ----
    state_file: Path = field(
        default_factory=lambda: Path(
            os.environ.get("HL_STATE_FILE", "./state/hl_quant_state.json")
        )
    )
    log_level: str = field(default_factory=lambda: os.environ.get("HL_LOG_LEVEL", "INFO"))

    @property
    def api_url(self) -> str:
        if self.network == "mainnet":
            return "https://api.hyperliquid.xyz"
        return "https://api.hyperliquid-testnet.xyz"

    @property
    def is_mainnet(self) -> bool:
        return self.network == "mainnet"

    @property
    def mainnet_armed(self) -> bool:
        return self.is_mainnet and self.mainnet_confirm == "YES_I_UNDERSTAND"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.network not in ("testnet", "mainnet"):
            errors.append(f"HL_NETWORK must be 'testnet' or 'mainnet', got {self.network!r}")
        if not self.api_wallet_private_key.startswith("0x") or len(self.api_wallet_private_key) != 66:
            errors.append("HL_API_WALLET_PRIVATE_KEY must be 0x-prefixed 66-char hex string")
        if not self.main_address.startswith("0x") or len(self.main_address) != 42:
            errors.append("HL_MAIN_ADDRESS must be 0x-prefixed 42-char hex address")
        if self.max_leverage > 10:
            errors.append(f"HL_MAX_LEVERAGE={self.max_leverage} exceeds hard cap of 10")
        if self.risk_per_trade > 0.01:
            errors.append(f"HL_RISK_PER_TRADE={self.risk_per_trade} exceeds hard cap of 0.01 (1%)")
        if self.max_position_frac > 0.5:
            errors.append(f"HL_MAX_POSITION_FRAC={self.max_position_frac} exceeds hard cap of 0.5")
        if self.daily_loss_limit > 0.05:
            errors.append(f"HL_DAILY_LOSS_LIMIT={self.daily_loss_limit} exceeds hard cap of 0.05 (5%)")
        if self.sweep_min_score < 0 or self.sweep_min_score > 1:
            errors.append("HL_SWEEP_MIN_SCORE must be in [0, 1]")
        if self.sweep_confirmation_bars < 1 or self.sweep_confirmation_bars > 4:
            errors.append("HL_SWEEP_CONFIRMATION_BARS must be in [1, 4]")
        if self.sl_slippage_bps <= 0 or self.sl_slippage_bps > 500:
            errors.append("HL_SL_SLIPPAGE_BPS must be in (0, 500]")
        if self.taker_fee_bps < 0 or self.taker_fee_bps > 50:
            errors.append("HL_TAKER_FEE_BPS must be in [0, 50]")
        if self.min_notional_usd < 1:
            errors.append("HL_MIN_NOTIONAL_USD must be >= 1")
        if self.decision_ttl_seconds < 5 or self.decision_ttl_seconds > 300:
            errors.append("HL_DECISION_TTL_SECONDS must be in [5, 300]")
        if self.higher_tf_ema_fast_period >= self.higher_tf_ema_slow_period:
            errors.append("higher timeframe EMA fast period must be below slow period")
        if self.trigger_ema_fast_period >= self.trigger_ema_slow_period:
            errors.append("trigger EMA fast period must be below slow period")
        if self.no_progress_bars < 1 or self.no_progress_bars > 16:
            errors.append("HL_NO_PROGRESS_BARS must be in [1, 16]")
        if self.is_mainnet and not self.mainnet_armed:
            errors.append(
                "HL_NETWORK=mainnet but HL_MAINNET_CONFIRM != 'YES_I_UNDERSTAND' "
                "(write paths will refuse)"
            )
        return errors


CONFIG = Config()
