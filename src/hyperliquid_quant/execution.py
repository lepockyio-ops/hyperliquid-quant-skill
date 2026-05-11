"""Hyperliquid execution layer.

Thin wrapper around the official `hyperliquid-python-sdk`. Provides:

* `HyperliquidClient` — singleton wrapper holding signing wallet + endpoints.
* Helpers to fetch account state, market data, candles, funding history.
* Helpers to place bracket orders (entry + SL + TP1 + TP2 atomically).

Note on Hyperliquid mechanics:
- ``Exchange`` class signs and submits actions.
- ``Info`` class fetches read-only data (no signature).
- API Wallets sign on behalf of the main account; we pass ``account_address``
  separately when needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eth_account import Account
from eth_account.signers.local import LocalAccount

# These imports are intentionally guarded so the file can be parsed for
# documentation / tests without the SDK installed. At runtime the import
# happens lazily inside the client constructor.
try:
    from hyperliquid.exchange import Exchange  # type: ignore
    from hyperliquid.info import Info  # type: ignore
    from hyperliquid.utils import constants  # type: ignore
    _HL_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    Exchange = None  # type: ignore
    Info = None  # type: ignore
    constants = None  # type: ignore
    _HL_SDK_AVAILABLE = False

from .config import Config


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float
    ts_ms: int


class HyperliquidClient:
    """Wraps Info + Exchange. One instance per process."""

    def __init__(self, config: Config):
        if not _HL_SDK_AVAILABLE:
            raise RuntimeError(
                "hyperliquid-python-sdk not installed. Run "
                "`pip install hyperliquid-python-sdk`."
            )
        errors = config.validate()
        if errors:
            raise ValueError("Invalid config:\n  - " + "\n  - ".join(errors))

        self.config = config
        self.wallet: LocalAccount = Account.from_key(config.api_wallet_private_key)

        api_url = (
            constants.MAINNET_API_URL if config.is_mainnet else constants.TESTNET_API_URL
        )

        self.info = Info(api_url, skip_ws=True)
        self.exchange = Exchange(
            wallet=self.wallet,
            base_url=api_url,
            account_address=config.main_address,
        )

    # =========================================================================
    # Read paths
    # =========================================================================

    def get_user_state(self) -> dict[str, Any]:
        """Returns full user state — equity, positions, margin, etc."""
        return self.info.user_state(self.config.main_address)

    def get_account_snapshot(self) -> dict[str, Any]:
        """Compact snapshot suitable for risk checks."""
        state = self.get_user_state()
        margin = state.get("marginSummary", {})
        equity = float(margin.get("accountValue", 0))
        margin_used = float(margin.get("totalMarginUsed", 0))

        positions: list[dict] = []
        for pos_wrap in state.get("assetPositions", []):
            pos = pos_wrap.get("position", {})
            size = float(pos.get("szi", 0))
            if size == 0:
                continue
            positions.append(
                {
                    "coin": pos.get("coin"),
                    "size": size,
                    "entry_px": float(pos.get("entryPx", 0)),
                    "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
                    "leverage": pos.get("leverage", {}).get("value"),
                }
            )

        return {
            "equity_usd": equity,
            "margin_used_usd": margin_used,
            "positions": positions,
        }

    def get_all_mids(self) -> dict[str, float]:
        """Returns {coin: mid_price} for every market."""
        raw = self.info.all_mids()
        return {k: float(v) for k, v in raw.items()}

    def get_meta(self) -> dict[str, Any]:
        """Universe metadata (asset IDs, max leverage, etc.)."""
        return self.info.meta()

    def get_candles(self, coin: str, interval: str, lookback_bars: int) -> list[Candle]:
        """Fetch recent candles. ``interval`` is one of "1m", "15m", "1h", etc."""
        # The SDK exposes candle_snapshot via Info; signature: (coin, interval, startTime, endTime)
        # Hyperliquid expects ms timestamps; we ask for the most recent N bars by
        # computing startTime from interval.
        interval_ms = _interval_to_ms(interval)
        end_ms = self._server_time_ms()
        start_ms = end_ms - interval_ms * (lookback_bars + 5)
        raw = self.info.candles_snapshot(coin, interval, start_ms, end_ms)
        out: list[Candle] = []
        for c in raw[-lookback_bars:]:
            out.append(
                Candle(
                    open=float(c["o"]),
                    high=float(c["h"]),
                    low=float(c["l"]),
                    close=float(c["c"]),
                    volume=float(c.get("v", 0)),
                    ts_ms=int(c["t"]),
                )
            )
        return out

    def get_funding_history(self, coin: str, lookback_hours: int = 96) -> list[float]:
        """Returns recent funding rates, oldest first. Hyperliquid settles hourly."""
        end_ms = self._server_time_ms()
        start_ms = end_ms - lookback_hours * 60 * 60 * 1000
        try:
            raw = self.info.funding_history(coin, start_ms, end_ms)
        except Exception:
            return []
        return [float(item.get("fundingRate", 0)) for item in raw]

    def get_open_interest_buckets(self, coin: str) -> dict[int, float]:
        """Approximate open-interest-by-leverage from L2 orderbook depth.

        Hyperliquid does not expose a per-leverage OI breakdown publicly, so we
        approximate by treating order book depth at the most-common margin
        levels as a proxy. This is a heuristic — replace with a better source
        if you have one (e.g. Coinglass).
        """
        try:
            l2 = self.info.l2_snapshot(coin)
            levels = l2.get("levels", [[], []])
            bids, asks = levels
            depth_below = sum(float(b["sz"]) * float(b["px"]) for b in bids)
            depth_above = sum(float(a["sz"]) * float(a["px"]) for a in asks)
        except Exception:
            return {}
        # Distribute proxy OI across common leverage buckets
        return {
            3: depth_below * 0.15 + depth_above * 0.15,
            5: depth_below * 0.30 + depth_above * 0.30,
            10: depth_below * 0.35 + depth_above * 0.35,
            20: depth_below * 0.20 + depth_above * 0.20,
        }

    def _server_time_ms(self) -> int:
        """Best-effort server time. Falls back to local clock."""
        try:
            return int(self.info.meta().get("serverTime", 0)) or _now_ms()
        except Exception:
            return _now_ms()

    # =========================================================================
    # Write paths
    # =========================================================================

    def set_leverage(self, coin: str, leverage: int) -> dict[str, Any]:
        """Set isolated leverage for a coin. Hyperliquid default is cross."""
        return self.exchange.update_leverage(leverage, coin, is_cross=False)

    def place_bracket_order(
        self,
        *,
        coin: str,
        is_buy: bool,
        size: float,
        entry_price: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float | None = None,
        leverage: int = 5,
    ) -> dict[str, Any]:
        """Place entry (limit IOC) + SL trigger + TP triggers in one call.

        Hyperliquid supports ``order_type`` of:
        - ``{"limit": {"tif": "Gtc"|"Ioc"|"Alo"}}``
        - ``{"trigger": {"isMarket": bool, "triggerPx": float, "tpsl": "sl"|"tp"}}``
        """
        self.set_leverage(coin, leverage)

        # Entry: aggressive limit (IOC). For higher fill probability you could
        # cross the book slightly; we use the requested price unchanged.
        entry_order = {
            "coin": coin,
            "is_buy": is_buy,
            "sz": size,
            "limit_px": entry_price,
            "order_type": {"limit": {"tif": "Ioc"}},
            "reduce_only": False,
        }

        sl_order = {
            "coin": coin,
            "is_buy": not is_buy,  # closing side
            "sz": size,
            "limit_px": stop_loss,
            "order_type": {
                "trigger": {"isMarket": True, "triggerPx": stop_loss, "tpsl": "sl"}
            },
            "reduce_only": True,
        }

        # TP1 closes half, TP2 closes the rest
        tp1_size = round(size / 2, 6)
        tp2_size = size - tp1_size

        tp1_order = {
            "coin": coin,
            "is_buy": not is_buy,
            "sz": tp1_size,
            "limit_px": take_profit_1,
            "order_type": {
                "trigger": {"isMarket": False, "triggerPx": take_profit_1, "tpsl": "tp"}
            },
            "reduce_only": True,
        }

        orders = [entry_order, sl_order, tp1_order]

        if take_profit_2 is not None and tp2_size > 0:
            tp2_order = {
                "coin": coin,
                "is_buy": not is_buy,
                "sz": tp2_size,
                "limit_px": take_profit_2,
                "order_type": {
                    "trigger": {
                        "isMarket": False,
                        "triggerPx": take_profit_2,
                        "tpsl": "tp",
                    }
                },
                "reduce_only": True,
            }
            orders.append(tp2_order)

        return self.exchange.bulk_orders(orders)

    def cancel(self, coin: str, oid: int) -> dict[str, Any]:
        return self.exchange.cancel(coin, oid)

    def close_position(self, coin: str) -> dict[str, Any]:
        """Reduce-only market close of any existing position in `coin`."""
        snapshot = self.get_account_snapshot()
        pos = next((p for p in snapshot["positions"] if p["coin"] == coin), None)
        if pos is None:
            return {"status": "no_position", "coin": coin}
        size = float(pos["size"])
        is_buy = size < 0  # we go opposite direction to close
        mid = self.get_all_mids().get(coin, 0)
        if mid <= 0:
            return {"status": "no_mid_price", "coin": coin}
        # Aggressive limit IOC = effectively market within slippage
        return self.exchange.order(
            name=coin,
            is_buy=is_buy,
            sz=abs(size),
            limit_px=mid * (1.01 if is_buy else 0.99),
            order_type={"limit": {"tif": "Ioc"}},
            reduce_only=True,
        )


# =============================================================================
# Helpers
# =============================================================================


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _interval_to_ms(interval: str) -> int:
    unit = interval[-1]
    n = int(interval[:-1])
    if unit == "m":
        return n * 60 * 1000
    if unit == "h":
        return n * 60 * 60 * 1000
    if unit == "d":
        return n * 24 * 60 * 60 * 1000
    raise ValueError(f"unsupported interval: {interval}")
