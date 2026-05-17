"""L4 risk guards + PnL reconciliation."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .config import Config
from .strategy import TradeDecision

UTC = timezone.utc
logger = logging.getLogger("hl_quant.risk")


@dataclass
class RiskCheckResult:
    passed: bool
    reasons: list[str]

    def to_dict(self) -> dict:
        return {"pass": self.passed, "reasons": self.reasons}


@dataclass
class AccountSnapshot:
    equity_usd: float
    margin_used_usd: float
    positions: list

    @property
    def margin_usage(self) -> float:
        if self.equity_usd <= 0:
            return 1.0
        return self.margin_used_usd / self.equity_usd


class StateStore:
    """Tracks rolling state: daily PnL, consecutive losses, cooldowns, open trades."""

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
            "last_fill_time_ms": 0,
            "open_trade": None,
        }

    def save(self) -> None:
        self.path.write_text(json.dumps(self._state, indent=2))

    def roll_day_if_needed(self) -> None:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if self._state.get("date_utc") != today:
            self._state["date_utc"] = today
            self._state["daily_realized_pnl"] = 0.0
            self._state["consec_losses"] = 0
            self._state["cooldown_until_utc"] = None
            self.save()

    @property
    def daily_pnl(self) -> float:
        self.roll_day_if_needed()
        return float(self._state.get("daily_realized_pnl", 0.0))

    @property
    def consec_losses(self) -> int:
        return int(self._state.get("consec_losses", 0))

    @property
    def cooldown_until(self) -> Optional[datetime]:
        raw = self._state.get("cooldown_until_utc")
        if raw is None:
            return None
        return datetime.fromisoformat(raw)

    @property
    def last_fill_time_ms(self) -> int:
        return int(self._state.get("last_fill_time_ms", 0))

    def set_last_fill_time_ms(self, ms: int) -> None:
        self._state["last_fill_time_ms"] = int(ms)
        self.save()

    @property
    def open_trade(self) -> Optional[dict]:
        return self._state.get("open_trade")

    def set_open_trade(self, info: Optional[dict]) -> None:
        self._state["open_trade"] = info
        self.save()

    def record_realized_pnl(self, pnl: float, config: Config) -> None:
        self.roll_day_if_needed()
        self._state["daily_realized_pnl"] = self.daily_pnl + pnl
        if pnl < 0:
            self._state["consec_losses"] = self.consec_losses + 1
            if self._state["consec_losses"] >= config.consec_loss_limit:
                cooldown_end = datetime.now(UTC) + timedelta(hours=config.cooldown_hours)
                self._state["cooldown_until_utc"] = cooldown_end.isoformat()
                logger.warning(
                    "consecutive-loss limit hit (%d losses) -> cooldown until %s",
                    self._state["consec_losses"],
                    self._state["cooldown_until_utc"],
                )
        else:
            self._state["consec_losses"] = 0
        self.save()


def startup_reconcile(client: Any, state: StateStore, config: Config) -> dict:
    """Reconcile local state with the exchange at startup."""
    del config
    issues: list[str] = []
    summary: dict[str, Any] = {}

    try:
        snap = client.get_account_snapshot()
        positions = snap.get("positions", [])
    except Exception as e:
        return {"error": f"snapshot failed: {e}"}

    try:
        open_orders = client.get_open_orders()
    except Exception as e:
        open_orders = []
        issues.append(f"could not list open orders: {e}")

    summary["positions_on_exchange"] = positions
    summary["open_orders_on_exchange"] = open_orders
    summary["state_open_trade"] = state.open_trade

    state_trade = state.open_trade
    if state_trade is not None:
        symbol = state_trade.get("symbol")
        has_pos = any(p.get("coin") == symbol for p in positions)
        if not has_pos:
            issues.append(
                f"state.open_trade references {symbol} but no live position - clearing local open_trade record"
            )
            state.set_open_trade(None)

    pos_coins = {p.get("coin") for p in positions}
    orphan = [o for o in open_orders if o.get("coin") not in pos_coins and o.get("reduceOnly")]
    if orphan:
        issues.append(
            f"{len(orphan)} orphan reduce-only order(s) for coins with no position; cancel manually via hl_cancel_order"
        )
        summary["orphan_orders"] = orphan

    summary["issues"] = issues
    return summary


def reconcile_pnl_from_fills(client: Any, state: StateStore, config: Config) -> dict:
    """Pull recent fills, sum realized PnL on closing legs, update state."""
    since = state.last_fill_time_ms
    fills = client.get_user_fills(since_ms=since + 1 if since else None)
    if not fills:
        return {"new_fills": 0, "realized_total": 0.0}

    fills.sort(key=lambda f: int(f.get("time", 0)))

    realized_total = 0.0
    counted = 0
    max_time = since
    for f in fills:
        t = int(f.get("time", 0))
        if t <= since:
            continue
        max_time = max(max_time, t)
        try:
            pnl = float(f.get("closedPnl", 0))
        except (TypeError, ValueError):
            pnl = 0.0
        if pnl == 0:
            continue
        state.record_realized_pnl(pnl, config)
        realized_total += pnl
        counted += 1

    if max_time > since:
        state.set_last_fill_time_ms(max_time)
    return {
        "new_fills": counted,
        "realized_total": realized_total,
        "cursor_ms": max_time,
    }


def check_trade(
    decision: TradeDecision,
    account: AccountSnapshot,
    state: StateStore,
    config: Config,
) -> RiskCheckResult:
    reasons: list[str] = []

    if decision.action == "HOLD":
        return RiskCheckResult(
            passed=False, reasons=["decision is HOLD, nothing to risk-check"]
        )

    if state.cooldown_until is not None and datetime.now(UTC) < state.cooldown_until:
        reasons.append(f"cooldown active until {state.cooldown_until.isoformat()}")

    daily_loss_limit_usd = account.equity_usd * config.daily_loss_limit
    if state.daily_pnl <= -daily_loss_limit_usd:
        reasons.append(
            f"daily loss limit breached: pnl={state.daily_pnl:.2f}, limit=-{daily_loss_limit_usd:.2f}"
        )

    if account.margin_usage >= config.max_margin_usage:
        reasons.append(
            f"margin usage {account.margin_usage:.2%} exceeds max {config.max_margin_usage:.2%}"
        )

    if any(p.get("coin") == decision.symbol for p in account.positions):
        reasons.append(f"already have a position in {decision.symbol}")

    if decision.stop_loss <= 0:
        reasons.append("stop_loss must be set and > 0")

    if decision.entry_price <= 0:
        reasons.append("entry_price must be > 0")

    if decision.size <= 0 or decision.notional <= 0:
        reasons.append("size/notional must be > 0")

    if decision.risk_amount <= 0:
        reasons.append("risk_amount must be > 0")
    elif decision.risk_amount > account.equity_usd * config.risk_per_trade * 1.05:
        reasons.append(
            f"risk_amount {decision.risk_amount:.2f} exceeds configured cap {account.equity_usd * config.risk_per_trade:.2f}"
        )

    if decision.leverage < 1 or decision.leverage > config.max_leverage:
        reasons.append(
            f"leverage {decision.leverage} outside allowed range [1, {config.max_leverage}]"
        )

    if decision.action == "LONG":
        if decision.stop_loss >= decision.entry_price:
            reasons.append("stop_loss for LONG is not below entry")
        if decision.take_profit_1 <= decision.entry_price:
            reasons.append("take_profit_1 for LONG must be above entry")
    elif decision.action == "SHORT":
        if decision.stop_loss <= decision.entry_price:
            reasons.append("stop_loss for SHORT is not above entry")
        if decision.take_profit_1 >= decision.entry_price:
            reasons.append("take_profit_1 for SHORT must be below entry")
    else:
        reasons.append(f"unsupported action {decision.action}")

    return RiskCheckResult(passed=not reasons, reasons=reasons)
