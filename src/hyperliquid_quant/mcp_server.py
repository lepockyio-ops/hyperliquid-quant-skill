"""MCP server entrypoint — registers 8 tools over stdio.

Run as: ``hl-quant-mcp`` (after ``pip install -e .``).

Any MCP client (OpenClaw, Claude Desktop, Cursor, Windsurf, etc.) can attach
to this stdio process and call the tools defined below.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .config import CONFIG, Config
from .execution import HyperliquidClient
from .risk import AccountSnapshot, StateStore, check_trade
from .signals import (
    SignalVector,
    atr,
    ema,
    funding_zscore,
    likely_liquidation_prices,
    nearest_cluster_distance_bps,
    reversal_candle,
)
from .strategy import evaluate

logger = logging.getLogger("hl_quant")


# =============================================================================
# Module-level singletons (lazy)
# =============================================================================

_client: HyperliquidClient | None = None
_state: StateStore | None = None


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


# =============================================================================
# Tool implementations (each returns a dict; MCP layer wraps to JSON text)
# =============================================================================


def tool_get_account_state() -> dict[str, Any]:
    """Read balance, positions, margin usage."""
    snap = _get_client().get_account_snapshot()
    state = _get_state()
    snap["daily_pnl"] = state.daily_pnl
    snap["consec_losses"] = state.consec_losses
    snap["cooldown_until_utc"] = (
        state.cooldown_until.isoformat() if state.cooldown_until else None
    )
    snap["network"] = CONFIG.network
    return snap


def tool_get_market_data(symbol: str) -> dict[str, Any]:
    """Mid, funding, 15m candles, orderbook depth approximation."""
    symbol = symbol.upper()
    if symbol not in CONFIG.universe:
        return {
            "error": f"{symbol} not in HL_UNIVERSE={CONFIG.universe}",
        }
    client = _get_client()
    mid = client.get_all_mids().get(symbol)
    candles_15m = client.get_candles(symbol, "15m", lookback_bars=100)
    funding = client.get_funding_history(symbol, lookback_hours=120)
    oi_buckets = client.get_open_interest_buckets(symbol)
    return {
        "symbol": symbol,
        "mid": mid,
        "candles_15m_count": len(candles_15m),
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
        "funding_history_len": len(funding),
        "latest_funding": funding[-1] if funding else None,
        "open_interest_buckets": oi_buckets,
    }


def tool_compute_signals(symbol: str) -> dict[str, Any]:
    """Run the deterministic signal vector for a symbol."""
    symbol = symbol.upper()
    if symbol not in CONFIG.universe:
        return {"error": f"{symbol} not in HL_UNIVERSE={CONFIG.universe}"}

    client = _get_client()
    candles_15m = client.get_candles(symbol, "15m", lookback_bars=100)
    if len(candles_15m) < 30:
        return {"error": f"insufficient candles for {symbol}: {len(candles_15m)}"}

    funding = client.get_funding_history(symbol, lookback_hours=120)
    fz = funding_zscore(funding, window=90)

    mid = client.get_all_mids().get(symbol, candles_15m[-1].close)

    oi_buckets = client.get_open_interest_buckets(symbol)
    long_clusters = likely_liquidation_prices(oi_buckets, mid, "long")
    short_clusters = likely_liquidation_prices(oi_buckets, mid, "short")
    near_long_bps = nearest_cluster_distance_bps(long_clusters, mid)
    near_short_bps = nearest_cluster_distance_bps(short_clusters, mid)

    closes = [c.close for c in candles_15m]
    highs = [c.high for c in candles_15m]
    lows = [c.low for c in candles_15m]
    ema_fast_v = ema(closes, 9)
    ema_slow_v = ema(closes, 21)
    atr_v = atr(highs, lows, closes, period=14)
    ratio = ema_fast_v / ema_slow_v if ema_slow_v else float("nan")
    last = candles_15m[-1]
    bullish = reversal_candle(last.open, last.close, "long")
    bearish = reversal_candle(last.open, last.close, "short")

    sv = SignalVector(
        symbol=symbol,
        current_price=mid,
        funding_zscore=fz,
        nearest_long_liq_bps=near_long_bps,
        nearest_short_liq_bps=near_short_bps,
        ema_fast=ema_fast_v,
        ema_slow=ema_slow_v,
        ema_ratio=ratio,
        atr_15m=atr_v,
        last_candle_bullish=bullish,
        last_candle_bearish=bearish,
    )
    return sv.to_dict()


def tool_evaluate_strategy(symbol: str) -> dict[str, Any]:
    """Compute signals + apply the formula in one call."""
    sig = tool_compute_signals(symbol)
    if "error" in sig:
        return sig
    snap = _get_client().get_account_snapshot()
    equity = float(snap["equity_usd"])
    sv = SignalVector(
        symbol=sig["symbol"],
        current_price=sig["current_price"],
        funding_zscore=sig["funding_zscore"],
        nearest_long_liq_bps=sig["nearest_long_liq_bps"],
        nearest_short_liq_bps=sig["nearest_short_liq_bps"],
        ema_fast=sig["ema_fast"],
        ema_slow=sig["ema_slow"],
        ema_ratio=sig["ema_ratio"],
        atr_15m=sig["atr_15m"],
        last_candle_bullish=sig["last_candle_bullish"],
        last_candle_bearish=sig["last_candle_bearish"],
    )
    decision = evaluate(sv, equity_usd=equity, config=CONFIG)
    return {
        "signals": sig,
        "decision": decision.to_dict(),
        "equity_usd": equity,
    }


def tool_risk_check(decision_json: str) -> dict[str, Any]:
    """Run all L4 guards against a candidate decision (passed as JSON)."""
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

    snap = _get_client().get_account_snapshot()
    account = AccountSnapshot(
        equity_usd=float(snap["equity_usd"]),
        margin_used_usd=float(snap["margin_used_usd"]),
        positions=snap["positions"],
    )
    result = check_trade(decision, account, _get_state(), CONFIG)
    return result.to_dict()


def tool_place_order(decision_json: str, override_risk: bool = False) -> dict[str, Any]:
    """Submit the decision to Hyperliquid as a bracket order.

    Refuses to execute unless risk_check passes. ``override_risk`` is accepted
    by signature but IGNORED — present to make accidental override attempts
    visible in logs.
    """
    if override_risk:
        logger.warning("override_risk=true received and IGNORED — no override allowed")

    rc = tool_risk_check(decision_json)
    if not rc.get("pass"):
        return {"status": "rejected_by_risk", "risk_check": rc}

    d = json.loads(decision_json)
    if d["action"] not in ("LONG", "SHORT"):
        return {"status": "rejected", "reason": f"action={d['action']} not actionable"}

    client = _get_client()
    is_buy = d["action"] == "LONG"
    result = client.place_bracket_order(
        coin=d["symbol"],
        is_buy=is_buy,
        size=float(d["size"]),
        entry_price=float(d["entry_price"]),
        stop_loss=float(d["stop_loss"]),
        take_profit_1=float(d["take_profit_1"]),
        take_profit_2=float(d.get("take_profit_2") or 0) or None,
        leverage=int(d["leverage"]),
    )
    return {"status": "submitted", "exchange_response": result}


def tool_cancel_order(symbol: str, oid: int) -> dict[str, Any]:
    return _get_client().cancel(symbol.upper(), int(oid))


def tool_close_position(symbol: str) -> dict[str, Any]:
    return _get_client().close_position(symbol.upper())


# =============================================================================
# MCP wiring
# =============================================================================

TOOL_DEFS: list[Tool] = [
    Tool(
        name="hl_get_account_state",
        description=(
            "Read Hyperliquid account state: equity, positions, margin usage, "
            "daily PnL, cooldown status. ALWAYS call before evaluating strategy."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="hl_get_market_data",
        description=(
            "Fetch mid price, funding history, 15m candles, and orderbook depth "
            "buckets for one symbol. Symbol must be in HL_UNIVERSE."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Hyperliquid coin name (e.g. BTC, ETH, SOL)",
                }
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="hl_compute_signals",
        description=(
            "Compute the deterministic signal vector: funding Z-score, liquidation "
            "cluster proximity (bps), EMA 9/21 ratio, ATR 14, last-candle reversal flag."
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
            "Apply the entry formula. Returns either LONG/SHORT with size, SL, TP1, "
            "TP2, leverage, or HOLD with reasons. The numbers returned are NOT "
            "negotiable — pass the decision unchanged to hl_risk_check then "
            "hl_place_order."
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
            "Run all L4 risk guards against a candidate decision JSON. Returns "
            "{pass: bool, reasons: [str]}. MUST pass before hl_place_order."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "decision_json": {
                    "type": "string",
                    "description": "JSON string of the TradeDecision returned by hl_evaluate_strategy",
                }
            },
            "required": ["decision_json"],
        },
    ),
    Tool(
        name="hl_place_order",
        description=(
            "Submit a decision as a Hyperliquid bracket order (entry + SL + TP1 + TP2). "
            "Re-runs hl_risk_check internally and refuses to execute if it fails."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "decision_json": {"type": "string"},
                "override_risk": {
                    "type": "boolean",
                    "description": "Has no effect; included so override attempts are visible in logs",
                    "default": False,
                },
            },
            "required": ["decision_json"],
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
        description=(
            "Emergency reduce-only market close of any existing position in `symbol`. "
            "Use ONLY when the user explicitly asks or in a documented black-swan."
        ),
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
                result = tool_risk_check(args["decision_json"])
            elif name == "hl_place_order":
                result = tool_place_order(
                    args["decision_json"], bool(args.get("override_risk", False))
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
    """Console-script entrypoint."""
    logging.basicConfig(
        level=getattr(logging, CONFIG.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Visibility on startup — but never log the private key.
    logger.info(
        "hl-quant-mcp starting | network=%s | universe=%s | main=%s",
        CONFIG.network,
        CONFIG.universe,
        CONFIG.main_address[:8] + "..." if CONFIG.main_address else "<unset>",
    )
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
