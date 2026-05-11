"""L4 risk guards. All checks run BEFORE any order is placed.

The state file (JSON) tracks daily PnL and consecutive losses across process
restarts. Every guard is independent — failing any one rejects the trade.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .strategy import TradeDecision

UTC = timezone.utc


@dataclass
class RiskCheckResult:
    passed: bool
    reasons: list

    def to_dict(self) -> dict:
        return {"pass": self.passed, "reasons": self.reasons}


@dataclass
class AccountSnapshot:
    """Minimal account info needed for risk checks. Source: execution layer."""

    equity_usd: float
    margin_used_usd: float
    positions: list  # [{coin: str, size: float, entry_px: float, unrealized_pnl: float}]

    @property
    def margin_usage(self) -> float:
        if self.equity_usd <= 0:
            return 1.0
        return self.margin_used_usd / self.equity_usd


# =============================================================================
# Persistent state
# =============================================================================


class StateStore:
    """Tracks rolling state: daily PnL, consecutive losses, cooldowns.

    Format on disk::

        {
            "date_utc": "2026-05-11",
            "daily_realized_pnl": -45.32,
            "consec_losses": 1,
            "cooldown_until_utc": null,
            "open_orders": {}
        }
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return self._fresh_state()
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return self._fresh_state()

    @staticmethod
    def _fresh_state() -> dict:
        return {
            "date_utc": datetime.now(UTC).strftime("%Y-%m-%d"),
            "daily_realized_pnl": 0.0,
            "consec_losses": 0,
            "cooldown_until_utc": None,
            "open_orders": {},
        }

    def save(self) -> None:
        self.path.write_text(json.dumps(self._state, indent=2))

    def roll_day_if_needed(self) -> None:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if self._state.get("date_utc") != today:
            self._state["date_utc"] = today
            self._state["daily_realized_pnl"] = 0.0
            self.save()

    @property
    def daily_pnl(self) -> float:
        self.roll_day_if_needed()
        return float(self._state.get("daily_realized_pnl", 0.0))

    @property
    def consec_losses(self) -> int:
        return int(self._state.get("consec_losses", 0))

    @property
    def cooldown_until(self):
        raw = self._state.get("cooldown_until_utc")
        if raw is None:
            return None
        return datetime.fromisoformat(raw)

    def record_realized_pnl(self, pnl: float, config: Config) -> None:
        self.roll_day_if_needed()
        self._state["daily_realized_pnl"] = self.daily_pnl + pnl
        if pnl < 0:
            self._state["consec_losses"] = self.consec_losses + 1
            if self._state["consec_losses"] >= config.consec_loss_limit:
                cooldown_end = datetime.now(UTC) + timedelta(hours=config.cooldown_hours)
                self._state["cooldown_until_utc"] = cooldown_end.isoformat()
        else:
            self._state["consec_losses"] = 0
        self.save()


# =============================================================================
# Guards
# =============================================================================


def check_trade(
    decision: TradeDecision,
    account: AccountSnapshot,
    state: StateStore,
    config: Config,
) -> RiskCheckResult:
    """Run every guard. Collect ALL failures, don't short-circuit — the agent
    should see the full picture for diagnostics."""
    reasons: list = []

    if decision.action == "HOLD":
        return RiskCheckResult(
            passed=False, reasons=["decision is HOLD, nothing to risk-check"]
        )

    # --- Guard 1: cooldown -------------------------------------------------
    if state.cooldown_until is not None and datetime.now(UTC) < state.cooldown_until:
        reasons.append(
            "in cooldown until "
            + state.cooldown_until.isoformat()
            + " after "
            + str(state.consec_losses)
            + " consecutive losses"
        )

    # --- Guard 2: daily loss limit ----------------------------------------
    daily_loss_threshold = -abs(config.daily_loss_limit) * account.equity_usd
    if state.daily_pnl <= daily_loss_threshold:
        reasons.append(
            f"daily PnL ${state.daily_pnl:.2f} <= limit ${daily_loss_threshold:.2f} "
            f"({config.daily_loss_limit:.1%} of equity)"
        )

    # --- Guard 3: margin usage --------------------------------------------
    if account.margin_usage > config.max_margin_usage:
        reasons.append(
            f"margin usage {account.margin_usage:.1%} > cap {config.max_margin_usage:.1%}"
        )

    # --- Guard 4: symbol already in position ------------------------------
    for pos in account.positions:
        if pos.get("coin") == decision.symbol and abs(float(pos.get("size", 0))) > 0:
            reasons.append(
                f"already have a position in {decision.symbol} "
                f"(size={pos['size']}); strategy forbids stacking"
            )
            break

    # --- Guard 5: leverage cap --------------------------------------------
    if decision.leverage > config.max_leverage:
        reasons.append(
            f"requested leverage {decision.leverage}x > cap {config.max_leverage}x"
        )

    # --- Guard 6: position size sanity ------------------------------------
    if decision.notional > account.equity_usd * config.max_leverage:
        reasons.append(
            f"notional ${decision.notional:.2f} exceeds equity x max_leverage "
            f"(${account.equity_usd * config.max_leverage:.2f})"
        )
    if decision.notional <= 0 or decision.size <= 0:
        reasons.append(
            f"degenerate sizing: notional={decision.notional}, size={decision.size}"
        )

    # --- Guard 7: stop-loss is mandatory ----------------------------------
    if decision.stop_loss <= 0:
        reasons.append("missing stop_loss; refused")

    # --- Guard 8: SL on correct side --------------------------------------
    if decision.action == "LONG" and decision.stop_loss >= decision.entry_price:
        reasons.append(
            f"LONG stop_loss {decision.stop_loss} not below entry {decision.entry_price}"
        )
    if decision.action == "SHORT" and decision.stop_loss <= decision.entry_price:
        reasons.append(
            f"SHORT stop_loss {decision.stop_loss} not above entry {decision.entry_price}"
        )

    return RiskCheckResult(passed=(len(reasons) == 0), reasons=reasons)
