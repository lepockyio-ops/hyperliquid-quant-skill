"""MCP server entrypoint — registers tools over stdio.

PATCH NOTES (critical fixes #1-#5 + severe fixes #6, #8, #9, #11, #14):
=======================================================================
- hl_get_account_state auto-reconciles PnL from fills, runs startup
  reconciliation on first call, and enforces max_hold_hours by auto-closing
  stale positions.
- hl_evaluate_strategy now returns a `decision_token` (single-use, TTL-bounded);
  the LLM cannot manufacture decisions any more.
- hl_place_order accepts ONLY a `decision_token` — the previous
  `decision_json` path is gone. This makes "formulaic, no LLM discretion"
  a hard property of the system, not just a prompt instruction.
- override_risk is a hard rejection.
- Mainnet writes are gated by HL_MAINNET_CONFIRM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .config import CONFIG
from .decision_cache import DecisionCache
from .execution import HyperliquidClient
from .risk import (
    AccountSnapshot,
    StateStore,
    check_trade,
    reconcile_pnl_from_fills,
    startup_reconcile,
)
from .signals import (
    SignalVector,
    atr,
    ema,
    funding_zscore,
    reversal_candle,
    sweep_scores,
)
from .strategy import evaluate

logger = logging.getLogger("hl_quant")


# =============================================================================
# Module-level singletons (lazy)
# =============================================================================

_client: HyperliquidClient | None = None
_state: StateStore | None = None
_cache: DecisionCache | None = None
_startup_reconciled = False


def _get_client() -> HyperliquidClient:
    global _client
    if _client is None:
        _client = HyperliquidClient(CONFIG)
    return _client


def _get_state() -> StateStore:
    global _state
    if _state is None:
        _state = StateStore(CONFIG.state_file)
    return _state


def _get_cache() -> DecisionCache:
    global _cache
    if _cache is None:
        _cache = DecisionCache(ttl_seconds=CONFIG.decision_ttl_seconds)
    return _cache


def _maybe_enforce_max_hold(client: HyperliquidClient, state: StateStore) -> dict | None:
    """PATCH (severe fix #8): if state.open_trade exceeds max_hold_hours,
    issue an emergency reduce-only close and clear state."""
    open_trade = state.open_trade
    if not open_trade:
        return None
    entry_ms = int(open_trade.get("entry_time_ms", 0))
    if entry_ms <= 0:
        return None
    age_hours = (time.time() * 1000 - entry_ms) / 3_600_000.0
    if age_hours < CONFIG.max_hold_hours:
        return None
    symbol = open_trade["symbol"]
    logger.warning(
        "max_hold_hours exceeded (%.2fh > %dh) for %s — auto-closing",
        age_hours, CONFIG.max_hold_hours, symbol,
    )
    try:
        result = client.close_position(symbol)
    except Exception as e:
        result = {"error": f"auto-close failed: {e}"}
    state.set_open_trade(None)
    return {
        "auto_closed": symbol,
        "age_hours": age_hours,
        "limit_hours": CONFIG.max_hold_hours,
        "exchange_response": result,
    }


def _cancel_open_trade_orders(client: HyperliquidClient, trade: dict) -> list[dict[str, Any]]:
    symbol = trade.get("symbol")
    if not symbol:
        return []
    oids = [
        int(oid)
        for oid in (
            trade.get("sl_oid"),
            trade.get("tp1_oid"),
            trade.get("tp2_oid"),
        )
        if oid
    ]
    if not oids:
        return []
    return client.cancel_many(symbol, oids)


def _maybe_manage_open_trade(client: HyperliquidClient, state: StateStore) -> dict | None:
    trade = state.open_trade
    if not trade:
        return None

    symbol = trade.get("symbol")
    if not symbol:
        state.set_open_trade(None)
        return {"status": "cleared_invalid_trade_state"}

    snap = client.get_account_snapshot()
    pos = next((p for p in snap["positions"] if p["coin"] == symbol), None)
    if pos is None or abs(float(pos.get("size", 0))) <= 0:
        state.set_open_trade(None)
        return {"status": "position_closed_on_exchange", "symbol": symbol}

    candles_15m = client.get_candles(symbol, "15m", lookback_bars=80)
    if len(candles_15m) < max(30, CONFIG.trail_ema_period + 2):
        return None

    closes = [c.close for c in candles_15m]
    highs = [c.high for c in candles_15m]
    lows = [c.low for c in candles_15m]
    atr_v = atr(highs, lows, closes, period=14)
    trail_ema = ema(closes, CONFIG.trail_ema_period)
    current_px = client.get_all_mids().get(symbol, closes[-1])

    is_long = trade.get("side") == "LONG"
    entry_px = float(trade.get("entry_px", 0))
    initial_stop = float(trade.get("stop_loss", 0))
    risk_per_unit = abs(entry_px - initial_stop)
    if risk_per_unit <= 0:
        return None

    best_price = float(trade.get("best_price", entry_px))
    if is_long:
        best_price = max(best_price, current_px)
        favorable_move = best_price - entry_px
        protective_candidate = max(
            entry_px * (1.0 + (CONFIG.breakeven_buffer_bps + 2 * CONFIG.taker_fee_bps) / 10000.0),
            trail_ema - atr_v * CONFIG.trail_atr_mult,
        )
    else:
        best_price = min(best_price, current_px)
        favorable_move = entry_px - best_price
        protective_candidate = min(
            entry_px * (1.0 - (CONFIG.breakeven_buffer_bps + 2 * CONFIG.taker_fee_bps) / 10000.0),
            trail_ema + atr_v * CONFIG.trail_atr_mult,
        )

    max_favorable_r = favorable_move / risk_per_unit
    trade["best_price"] = best_price
    trade["max_favorable_r"] = max(
        float(trade.get("max_favorable_r", 0.0)),
        max_favorable_r,
    )

    entry_time_ms = int(trade.get("entry_time_ms", 0))
    age_bars = 0
    if entry_time_ms > 0:
        age_bars = int((time.time() * 1000 - entry_time_ms) / (15 * 60 * 1000))

    if age_bars >= CONFIG.no_progress_bars and trade["max_favorable_r"] < CONFIG.no_progress_min_rr:
        cancel_results = _cancel_open_trade_orders(client, trade)
        close_result = client.close_position(symbol)
        state.set_open_trade(None)
        return {
            "status": "closed_no_progress",
            "symbol": symbol,
            "age_bars": age_bars,
            "max_favorable_r": trade["max_favorable_r"],
            "cancel_results": cancel_results,
            "exchange_response": close_result,
        }

    if trade["max_favorable_r"] >= CONFIG.trail_activation_rr:
        live_size = abs(float(pos["size"]))
        current_stop = float(trade.get("active_stop_loss", initial_stop))
        improve = (
            protective_candidate > current_stop if is_long else protective_candidate < current_stop
        )
        if improve:
            cancel_results = []
            if trade.get("sl_oid"):
                cancel_results = client.cancel_many(symbol, [int(trade["sl_oid"])])
            sl_resp = client.place_stop_loss(
                coin=symbol,
                is_long=is_long,
                size=live_size,
                trigger_px=protective_candidate,
            )
            oid = None
            try:
                statuses = sl_resp["response"]["data"]["statuses"]
                for s in statuses:
                    for key in ("resting", "filled"):
                        if key in s and "oid" in s[key]:
                            oid = int(s[key]["oid"])
                            break
                    if oid is not None:
                        break
            except Exception:
                oid = None
            trade["sl_oid"] = oid
            trade["active_stop_loss"] = protective_candidate
            trade["trail_armed"] = True
            state.set_open_trade(trade)
            return {
                "status": "trail_stop_updated",
                "symbol": symbol,
                "new_stop_loss": protective_candidate,
                "cancel_results": cancel_results,
            }

    state.set_open_trade(trade)
    return None


def _build_signal_vector(client: HyperliquidClient, symbol: str) -> SignalVector | dict[str, Any]:
    if symbol not in CONFIG.universe:
        return {"error": f"{symbol} not in HL_UNIVERSE={CONFIG.universe}"}
    candles_15m = client.get_candles(symbol, "15m", lookback_bars=120)
    candles_1h = client.get_candles(symbol, "1h", lookback_bars=80)
    min_15m = max(30, CONFIG.sweep_lookback + CONFIG.sweep_confirmation_bars + 2)
    min_1h = max(
        CONFIG.higher_tf_ema_fast_period,
        CONFIG.higher_tf_ema_slow_period,
    )
    if len(candles_15m) < min_15m:
        return {"error": f"insufficient 15m candles for {symbol}: {len(candles_15m)}"}
    if len(candles_1h) < min_1h:
        return {"error": f"insufficient 1h candles for {symbol}: {len(candles_1h)}"}

    funding = client.get_funding_history(symbol, lookback_hours=120)
    fz = funding_zscore(funding, window=90)
    mid = client.get_all_mids().get(symbol, candles_15m[-1].close)

    closes_15m = [c.close for c in candles_15m]
    highs_15m = [c.high for c in candles_15m]
    lows_15m = [c.low for c in candles_15m]
    opens_15m = [c.open for c in candles_15m]
    closes_1h = [c.close for c in candles_1h]

    long_score, short_score, long_age, short_age = sweep_scores(
        highs_15m,
        lows_15m,
        opens_15m,
        closes_15m,
        sweep_lookback=CONFIG.sweep_lookback,
        confirmation_bars=CONFIG.sweep_confirmation_bars,
    )

    ema_fast_v = ema(closes_15m, CONFIG.trigger_ema_fast_period)
    ema_slow_v = ema(closes_15m, CONFIG.trigger_ema_slow_period)
    ema_htf_fast = ema(closes_1h, CONFIG.higher_tf_ema_fast_period)
    ema_htf_slow = ema(closes_1h, CONFIG.higher_tf_ema_slow_period)
    atr_v = atr(highs_15m, lows_15m, closes_15m, period=14)
    ratio = ema_fast_v / ema_slow_v if ema_slow_v else float("nan")
    htf_ratio = ema_htf_fast / ema_htf_slow if ema_htf_slow else float("nan")
    last = candles_15m[-1]
    bullish = reversal_candle(last.open, last.close, "long")
    bearish = reversal_candle(last.open, last.close, "short")

    return SignalVector(
        symbol=symbol,
        current_price=mid,
        funding_zscore=fz,
        sweep_long_score=long_score,
        sweep_short_score=short_score,
        sweep_long_age_bars=long_age,
        sweep_short_age_bars=short_age,
        ema_fast=ema_fast_v,
        ema_slow=ema_slow_v,
        ema_ratio=ratio,
        ema_htf_fast=ema_htf_fast,
        ema_htf_slow=ema_htf_slow,
        ema_htf_ratio=htf_ratio,
        atr_15m=atr_v,
        last_candle_bullish=bullish,
        last_candle_bearish=bearish,
    )


# =============================================================================
# Tool implementations
# =============================================================================


def tool_get_account_state() -> dict[str, Any]:
    """Read balance, positions, margin usage. PATCH: reconciles PnL,
    enforces max_hold_hours, and runs startup reconciliation on first call."""
    global _startup_reconciled
    client = _get_client()
    state = _get_state()

    startup_info = None
    if not _startup_reconciled:
        try:
            startup_info = startup_reconcile(client, state, CONFIG)
        except Exception as e:
            startup_info = {"error": str(e)}
        _startup_reconciled = True

    # Reconcile PnL from fills (every call)
    try:
        recon = reconcile_pnl_from_fills(client, state, CONFIG)
    except Exception as e:
        recon = {"error": str(e)}

    try:
        managed_trade = _maybe_manage_open_trade(client, state)
    except Exception as e:
        managed_trade = {"error": f"trade management failed: {e}"}

    auto_close = _maybe_enforce_max_hold(client, state)

    snap = client.get_account_snapshot()
    snap["daily_pnl"] = state.daily_pnl
    snap["consec_losses"] = state.consec_losses
    snap["cooldown_until_utc"] = (
        state.cooldown_until.isoformat() if state.cooldown_until else None
    )
    snap["network"] = CONFIG.network
    snap["mainnet_armed"] = CONFIG.mainnet_armed
    snap["reconciliation"] = recon
    snap["decisions_pending"] = _get_cache().peek_count()
    if managed_trade is not None:
        snap["trade_management"] = managed_trade
    if startup_info is not None:
        snap["startup_reconciliation"] = startup_info
    if auto_close is not None:
        snap["max_hold_auto_close"] = auto_close
    return snap


def tool_get_market_data(symbol: str) -> dict[str, Any]:
    symbol = symbol.upper()
    if symbol not in CONFIG.universe:
        return {"error": f"{symbol} not in HL_UNIVERSE={CONFIG.universe}"}
    client = _get_client()
    mid = client.get_all_mids().get(symbol)
    candles_15m = client.get_candles(symbol, "15m", lookback_bars=100)
    candles_1h = client.get_candles(symbol, "1h", lookback_bars=80)
    funding = client.get_funding_history(symbol, lookback_hours=120)
    return {
        "symbol": symbol,
        "mid": mid,
        "candles_15m_count": len(candles_15m),
        "candles_1h_count": len(candles_1h),
        "last_candle": (
            {
                "open": candles_15m[-1].open,
                "high": candles_15m[-1].high,
                "low": candles_15m[-1].low,
                "close": candles_15m[-1].close,
                "volume": candles_15m[-1].volume,
            }
            if candles_15m
            else None
        ),
        "last_1h_close": candles_1h[-1].close if candles_1h else None,
        "funding_history_len": len(funding),
        "latest_funding": funding[-1] if funding else None,
    }


def tool_compute_signals(symbol: str) -> dict[str, Any]:
    symbol = symbol.upper()
    if symbol not in CONFIG.universe:
        return {"error": f"{symbol} not in HL_UNIVERSE={CONFIG.universe}"}

    client = _get_client()
    sv = _build_signal_vector(client, symbol)
    if isinstance(sv, dict):
        return sv
    return sv.to_dict()


def tool_evaluate_strategy(symbol: str) -> dict[str, Any]:
    """PATCH (severe fix #6): now issues a single-use decision_token."""
    client = _get_client()
    sv = _build_signal_vector(client, symbol.upper())
    if isinstance(sv, dict):
        return sv
    snap = client.get_account_snapshot()
    equity = float(snap["equity_usd"])
    decision = evaluate(sv, equity_usd=equity, config=CONFIG)
    token = None
    if decision.action in ("LONG", "SHORT"):
        token = _get_cache().issue(decision)
    return {
        "signals": sv.to_dict(),
        "decision": decision.to_dict(),
        "decision_token": token,
        "ttl_seconds": CONFIG.decision_ttl_seconds,
        "equity_usd": equity,
    }


def tool_risk_check(decision_token: str | None = None, decision_json: str | None = None) -> dict[str, Any]:
    """PATCH (severe fix #6): risk-check is keyed off the cached decision,
    not free-form JSON. For backward compatibility we accept either, but the
    token path is the only one place_order will subsequently honour."""
    decision = None
    if decision_token:
        decision = _get_cache().peek(decision_token)
    if decision is None and decision_json:
        try:
            d = json.loads(decision_json)
        except json.JSONDecodeError as e:
            return {"pass": False, "reasons": [f"invalid decision JSON: {e}"]}
        from .strategy import TradeDecision
        decision = TradeDecision(
            action=d.get("action", "HOLD"),
            symbol=d.get("symbol", ""),
            reason=d.get("reason", ""),
            entry_price=float(d.get("entry_price", 0)),
            size=float(d.get("size", 0)),
            notional=float(d.get("notional", 0)),
            stop_loss=float(d.get("stop_loss", 0)),
            take_profit_1=float(d.get("take_profit_1", 0)),
            take_profit_2=float(d.get("take_profit_2", 0)),
            leverage=int(d.get("leverage", 1)),
            risk_amount=float(d.get("risk_amount", 0)),
            sl_distance=float(d.get("sl_distance", 0)),
        )
    if decision is None:
        return {"pass": False, "reasons": ["no decision_token or decision_json provided"]}

    snap = _get_client().get_account_snapshot()
    account = AccountSnapshot(
        equity_usd=float(snap["equity_usd"]),
        margin_used_usd=float(snap["margin_used_usd"]),
        positions=snap["positions"],
    )
    result = check_trade(decision, account, _get_state(), CONFIG)
    return result.to_dict()


def tool_place_order(decision_token: str, override_risk: bool = False) -> dict[str, Any]:
    """PATCH (severe fix #6 + #9): decision_token only — no raw decision_json.

    The token must come from a previous call to hl_evaluate_strategy and
    must not have expired (TTL=decision_ttl_seconds). The token is single-use:
    once redeemed it cannot be replayed.
    """
    if override_risk:
        return {
            "status": "rejected",
            "reason": "override_risk is not permitted in any version of this skill",
        }
    if not decision_token:
        return {"status": "rejected", "reason": "decision_token required"}

    cache = _get_cache()
    decision = cache.redeem(decision_token)
    if decision is None:
        return {
            "status": "rejected_token",
            "reason": (
                "decision_token is unknown, already consumed, or expired. "
                "Re-run hl_evaluate_strategy to obtain a fresh token."
            ),
        }

    # Re-run risk check on the *stored* decision (untamperable by LLM)
    snap = _get_client().get_account_snapshot()
    account = AccountSnapshot(
        equity_usd=float(snap["equity_usd"]),
        margin_used_usd=float(snap["margin_used_usd"]),
        positions=snap["positions"],
    )
    rc = check_trade(decision, account, _get_state(), CONFIG)
    if not rc.passed:
        return {"status": "rejected_by_risk", "risk_check": rc.to_dict()}

    if decision.action not in ("LONG", "SHORT"):
        return {"status": "rejected", "reason": f"action={decision.action} not actionable"}

    client = _get_client()
    state = _get_state()
    is_buy = decision.action == "LONG"
    bracket = client.place_bracket_order(
        coin=decision.symbol,
        is_buy=is_buy,
        size=decision.size,
        entry_price=decision.entry_price,
        stop_loss=decision.stop_loss,
        take_profit_1=decision.take_profit_1,
        take_profit_2=decision.take_profit_2 or None,
        leverage=decision.leverage,
    )

    result_dict = bracket.to_dict()

    if bracket.entry_status == "filled":
        actual_stop = (
            bracket.entry_fill_avg_px - decision.sl_distance
            if is_buy
            else bracket.entry_fill_avg_px + decision.sl_distance
        )
        actual_tp1 = (
            bracket.entry_fill_avg_px + decision.sl_distance * CONFIG.tp1_rr
            if is_buy
            else bracket.entry_fill_avg_px - decision.sl_distance * CONFIG.tp1_rr
        )
        actual_tp2 = 0.0
        if decision.take_profit_2:
            tp2_dist = abs(decision.take_profit_2 - decision.entry_price)
            actual_tp2 = (
                bracket.entry_fill_avg_px + tp2_dist
                if is_buy
                else bracket.entry_fill_avg_px - tp2_dist
            )
        state.set_open_trade({
            "symbol": decision.symbol,
            "side": decision.action,
            "size": bracket.entry_fill_size,
            "initial_size": bracket.entry_fill_size,
            "entry_px": bracket.entry_fill_avg_px,
            "entry_time_ms": int(time.time() * 1000),  # PATCH #8: stamp for max_hold
            "stop_loss": actual_stop,
            "active_stop_loss": actual_stop,
            "take_profit_1": actual_tp1,
            "take_profit_2": actual_tp2,
            "best_price": bracket.entry_fill_avg_px,
            "max_favorable_r": 0.0,
            "trail_armed": False,
            "sl_oid": bracket.sl_oid,
            "tp1_oid": bracket.tp1_oid,
            "tp2_oid": bracket.tp2_oid,
        })

    if bracket.entry_status == "unfilled":
        result_dict["status"] = "entry_unfilled"
    elif not bracket.position_protected:
        result_dict["status"] = "POSITION_UNPROTECTED_EMERGENCY_CLOSE"
        logger.error("position unprotected; emergency-close attempted: %s", result_dict)
    else:
        result_dict["status"] = "submitted_and_protected"
    return result_dict


def tool_cancel_order(symbol: str, oid: int) -> dict[str, Any]:
    return _get_client().cancel(symbol.upper(), int(oid))


def tool_close_position(symbol: str) -> dict[str, Any]:
    state = _get_state()
    result = _get_client().close_position(symbol.upper())
    # Clear open_trade record if it matches
    ot = state.open_trade
    if ot and ot.get("symbol") == symbol.upper():
        state.set_open_trade(None)
    return result


# =============================================================================
# MCP wiring
# =============================================================================

TOOL_DEFS: list[Tool] = [
    Tool(
        name="hl_get_account_state",
        description=(
            "Read Hyperliquid account state: equity, positions, margin usage, "
            "daily PnL, cooldown status. Auto-reconciles PnL from fills, "
            "enforces max_hold_hours by auto-closing stale positions, and "
            "runs startup reconciliation on first call. ALWAYS call before "
            "evaluating strategy."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="hl_get_market_data",
        description=(
            "Fetch mid price, funding history, plus 15m and 1h candles for one symbol."
        ),
        inputSchema={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    ),
    Tool(
        name="hl_compute_signals",
        description=(
            "Compute the deterministic signal vector: funding filter, "
            "recent sweep scores, 15m/1h EMA ratios, ATR 14, and reversal flags."
        ),
        inputSchema={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    ),
    Tool(
        name="hl_evaluate_strategy",
        description=(
            "Apply the entry formula. For LONG/SHORT setups, returns a "
            "`decision_token` (TTL seconds, single-use) that must be passed "
            "to hl_place_order. For HOLD, returns the reasons but no token."
        ),
        inputSchema={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    ),
    Tool(
        name="hl_risk_check",
        description=(
            "Inspect a candidate decision against the L4 guards. Accepts "
            "decision_token (preferred) or decision_json (read-only)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "decision_token": {"type": "string"},
                "decision_json": {"type": "string"},
            },
            "required": [],
        },
    ),
    Tool(
        name="hl_place_order",
        description=(
            "Submit the decision identified by decision_token as a Hyperliquid "
            "bracket order. Token must come from a recent hl_evaluate_strategy "
            "call and is single-use."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "decision_token": {"type": "string"},
                "override_risk": {
                    "type": "boolean",
                    "description": "Always rejected. Do not set.",
                    "default": False,
                },
            },
            "required": ["decision_token"],
        },
    ),
    Tool(
        name="hl_cancel_order",
        description="Cancel a single order by oid.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "oid": {"type": "integer"},
            },
            "required": ["symbol", "oid"],
        },
    ),
    Tool(
        name="hl_close_position",
        description="Emergency reduce-only market close of any existing position in symbol.",
        inputSchema={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    ),
]


async def _serve() -> None:
    server: Server = Server("hyperliquid-quant")

    @server.list_tools()
    async def _list() -> list[Tool]:
        return TOOL_DEFS

    @server.call_tool()
    async def _call(name: str, args: dict[str, Any]) -> list[TextContent]:
        try:
            if name == "hl_get_account_state":
                result = tool_get_account_state()
            elif name == "hl_get_market_data":
                result = tool_get_market_data(args["symbol"])
            elif name == "hl_compute_signals":
                result = tool_compute_signals(args["symbol"])
            elif name == "hl_evaluate_strategy":
                result = tool_evaluate_strategy(args["symbol"])
            elif name == "hl_risk_check":
                result = tool_risk_check(
                    decision_token=args.get("decision_token"),
                    decision_json=args.get("decision_json"),
                )
            elif name == "hl_place_order":
                result = tool_place_order(
                    args["decision_token"], bool(args.get("override_risk", False))
                )
            elif name == "hl_cancel_order":
                result = tool_cancel_order(args["symbol"], args["oid"])
            elif name == "hl_close_position":
                result = tool_close_position(args["symbol"])
            else:
                result = {"error": f"unknown tool: {name}"}
        except Exception as e:
            logger.exception("tool %s failed", name)
            result = {"error": f"{type(e).__name__}: {e}"}
        return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, CONFIG.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "hl-quant-mcp starting | network=%s | mainnet_armed=%s | universe=%s",
        CONFIG.network, CONFIG.mainnet_armed, CONFIG.universe,
    )
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
