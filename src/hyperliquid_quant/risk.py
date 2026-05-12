"""L4 risk guards + PnL reconciliation.

PATCH NOTES (critical-issue fix #3):
====================================
The original module defined `record_realized_pnl` but it was never
called from anywhere, so the daily-loss cap and consecutive-loss
cooldown never fired.

This patched version adds `reconcile_pnl_from_fills(client, state)`
which is called on every `hl_get_account_state` request (see
mcp_server.py). It pulls fills since the last reconciliation, sums
the `closedPnl` field for reduce-only fills, and feeds the totals
to `record_realized_pnl`.

State file gets two new fields:
    "last_fill_time_ms": int   — fills before this are already counted
    "open_trade":       dict   — info about the live entry (oid, side, ...)

Everything else is unchanged from the original.
"""

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
    reasons: list

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


# =============================================================================
# Persistent state
# =============================================================================


class StateStore:
    """Tracks rolling state: daily PnL, consecutive losses, cooldowns,
    last reconciliation cursor, open trades.
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
        """Called by reconcile_pnl_from_fills for each closed trade."""
        self.roll_day_if_needed()
        self._state["daily_realized_pnl"] = self.daily_pnl + pnl
        if pnl < 0:
            self._state["consec_losses"] = self.consec_losses + 1
            if self._state["consec_losses"] >= config.consec_loss_limit:
                cooldown_end = datetime.now(UTC) + timedelta(hours=config.cooldown_hours)
                self._state["cooldown_until_utc"] = cooldown_end.isoformat()
                logger.warning(
                    "consecutive-loss limit hit (%d losses) → cooldown until %s",
                    self._state["consec_losses"],
                    self._state["cooldown_until_utc"],
                )
        else:
            self._state["consec_losses"] = 0
        self.save()
        logger.info(
            "recorded PnL=%.2f, daily=%.2f, consec_losses=%d",
            pnl, self._state["daily_realized_pnl"], self._state["consec_losses"],
        )


# =============================================================================
# PATCH (fix #3): PnL reconciler
# =============================================================================


def startup_reconcile(client: Any, state: StateStore, config: Config) -> dict:
    """Reconcile local state with the exchange at startup.

    PATCH (severe fix #14): on process restart we may have:
      - open positions on the exchange that the local state knows nothing about
      - resting SL/TP orders from a previous (crashed) session
      - a `state.open_trade` referring to a position that no longer exists

    We surface these to the agent as a diagnostic dict. We do NOT auto-cancel
    or auto-close — that's a deliberate decision so the operator sees the
    drift first. The `tool_get_account_state` path now includes this on the
    first call after startup.
    """
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
                f"state.open_trade references {symbol} but no live position — "
                f"clearing local open_trade record"
            )
            state.set_open_trade(None)

    # Orphaned reduce-only orders for symbols where we have no position
    pos_coins = {p.get("coin") for p in positions}
    orphan = [o for o in open_orders if o.get("coin") not in pos_coins and o.get("reduceOnly")]
    if orphan:
        issues.append(
            f"{len(orphan)} orphan reduce-only order(s) for coins with no position; "
            f"cancel manually via hl_cancel_order"
        )
        summary["orphan_orders"] = orphan

    summary["issues"] = issues
    return summary


def reconcile_pnl_from_fills(client: Any, state: StateStore, config: Config) -> dict:
    """Pull recent fills, sum realized PnL on closing legs, update state.

    Hyperliquid `info.user_fills(address)` returns objects with at least:
        {"time": ms, "closedPnl": str, "dir": str, "coin": str,
         "side": "B"|"A", "px": str, "sz": str, "oid": int, ...}

    We only count fills with `closedPnl` != 0 (i.e. reduce-only fills that
    actually closed some inventory). We aggregate per-fill and feed each
    delta to ``state.record_realized_pnl`` so that consecutive-loss logic
    sees each trade as a separate event (one bracket usually produces 1-2
    closing fills — SL, TP1, TP2 — we treat each as a separate event).

    Returns a small summary for diagnostics.
    """
    since = state.last_fill_time_ms
    fills = client.get_user_fills(since_ms=since + 1 if since else None)
    if not fills:
        return {"new_fills": 0, "realized_total": 0.0}

    # Sort oldest-first so we update in chronological order
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
            # Open or partial-non-closing fill — skip
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


# =============================================================================
# Guards (unchanged from original)
# =============================================================================


def check_trade(
    decision: TradeDecision,
    account: AccountSnapshot,
    state: StateStore,
    config: Config,
) -> RiskCheckResult:
    reasons: list = []

    if decision.action == "HOLD":
        return RiskCheckResult(
            passed=False, reasons=["decision is HOLD, nothing to risk-check"]
        )

    if state.cooldown_until is not None and datetime.now(UTC) < state.cooldown_until:
        rea