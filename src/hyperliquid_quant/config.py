"""Environment-driven config. Pure data, no side effects beyond reading os.environ."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env if it exists. Silent if missing (env vars may come from process).
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
    # Network
    network: str = field(default_factory=lambda: os.environ.get("HL_NETWORK", "testnet"))
    api_wallet_private_key: str = field(
        default_factory=lambda: os.environ.get("HL_API_WALLET_PRIVATE_KEY", "")
    )
    main_address: str = field(default_factory=lambda: os.environ.get("HL_MAIN_ADDRESS", ""))

    # Risk
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

    # Strategy
    funding_z_threshold: float = field(
        default_factory=lambda: _env_float("HL_FUNDING_Z_THRESHOLD", 2.0)
    )
    liq_proximity_bps: float = field(
        default_factory=lambda: _env_float("HL_LIQ_PROXIMITY_BPS", 10)
    )
    sl_atr_mult: float = field(default_factory=lambda: _env_float("HL_SL_ATR_MULT", 1.2))
    sl_min_frac: float = field(default_factory=lambda: _env_float("HL_SL_MIN_FRAC", 0.003))
    tp1_rr: float = field(default_factory=lambda: _env_float("HL_TP1_RR", 1.5))
    universe: list[str] = field(
        default_factory=lambda: _env_list("HL_UNIVERSE", ["BTC", "ETH", "SOL"])
    )

    # Operational
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

    def validate(self) -> list[str]:
        """Return list of validation errors. Empty list means OK."""
        errors: list[str] = []
        if self.network not in ("testnet", "mainnet"):
            errors.append(f"HL_NETWORK must be 'testnet' or 'mainnet', got {self.network!r}")
        if not self.api_wallet_private_key.startswith("0x") or len(
            self.api_wallet_private_key
        ) != 66:
            errors.append("HL_API_WALLET_PRIVATE_KEY must be 0x-prefixed 66-char hex string")
        if not self.main_address.startswith("0x") or len(self.main_address) != 42:
            errors.append("HL_MAIN_ADDRESS must be 0x-prefixed 42-char hex address")
        if self.max_leverage > 10:
            errors.append(f"HL_MAX_LEVERAGE={self.max_leverage} exceeds hard cap of 10")
        if self.risk_per_trade > 0.01:
            errors.append(
                f"HL_RISK_PER_TRADE={self.risk_per_trade} exceeds hard cap of 0.01 (1%)"
            )
        if self.max_position_frac > 0.5:
            errors.append(
                f"HL_MAX_POSITION_FRAC={self.max_position_frac} exceeds hard cap of 0.5"
            )
        return errors


# Singleton — loaded once per process.
CONFIG = Config()
