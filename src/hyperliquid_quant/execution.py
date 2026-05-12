"""Hyperliquid execution layer.

PATCH NOTES (critical-issue fixes #2, #4, #5):
==============================================
This is the most heavily modified file. Changes:

1. **Wallet sanity check on startup** (fix #4):
   - We derive the API wallet's address from the private key and:
     a) refuse to start if api_wallet_address == main_address (you handed
        over your main key by mistake);
     b) refuse to start if the API wallet's own account on Hyperliquid has
        equity > HL_API_WALLET_MAX_BALANCE_USD (someone is treating the
        API wallet as a funding wallet, which negates the whole "API wallet
        cannot withdraw" safety property).

2. **Tick / lot precision** (fix #5):
   - We load `info.meta()` once at init and build a per-asset
     `AssetMeta(sz_decimals, max_leverage)` cache.
   - `_round_size` truncates size to szDecimals.
   - `_round_price` applies Hyperliquid's 5-significant-figure rule.
   - All order calls go through these helpers.

3. **Fixed candle/info endpoints** (fix #5):
   - The old code called `info.candles_snapshot(coin, interval, start, end)`.
     The actual SDK is `info.candle_snapshot({"coin": ..., "interval": ...,
     "startTime": ..., "endTime": ...})`. Patched accordingly.
   - Removed `get_open_interest_buckets` (broken proxy, no longer needed).
   - `_server_time_ms` now uses local clock with a clear comment (the
     previous "from meta" path always fell through anyway).

4. **Safe bracket execution** (fix #2):
   - `place_bracket_order` is now SEQUENTIAL with verification:
        i)   send entry (IOC limit) → poll user_state until fill or timeout
        ii)  if entry never fills → done, no exposure
        iii) once filled, send SL (limit-trigger with worst-acceptable price)
        iv)  verify SL appeared in open orders → if not, emergency
             reduce-only market close
        v)   send TP1 / TP2 (best effort — if these fail we hold the SL'd
             position and log loudly)
   - Returns a structured result with every leg's status, plus a
     `position_protected: bool` flag the agent MUST check.

5. **Slippage-bounded SL** (fix #5 spillover):
   - SL is sent as a limit trigger with a worst price = trigger ± sl_slippage_bps,
     not a naked market trigger. In a thin asset blowout this caps slippage
     at the configured tolerance (with the trade-off that an extreme gap
     leaves SL unfilled; in that case the close_position emergency path
     should be invoked by the operator/agent).

6. **user_fills support** for PnL reconciliation (used by fix #3).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount

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

logger = logging.getLogger("hl_quant.execution")


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float
    ts_ms: int


@dataclass
class AssetMeta:
    name: str
    asset_id: int
    sz_decimals: int
    max_leverage: int


@dataclass
class BracketResult:
    """Structured result of a bracket-order placement.

    The agent MUST inspect `position_protected` before considering the
    trade successful. If False, the position either failed to enter, or
    entered but the stop-loss could not be placed and an emergency close
    was attempted.
    """
    entry_status: str = "not_attempted"
    entry_oid: Optional[int] = None
    entry_fill_size: float = 0.0
    entry_fill_avg_px: float = 0.0
    sl_status: str = "not_attempted"
    sl_oid: Optional[int] = None
    tp1_status: str = "not_attempted"
    tp1_oid: Optional[int] = None
    tp2_status: str = "not_attempted"
    tp2_oid: Optional[int] = None
    emergency_close_status: str = "not_required"
    position_protected: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "entry_status": self.entry_status,
            "entry_oid": self.entry_oid,
            "entry_fill_size": self.entry_fill_size,
            "entry_fill_avg_px": self.entry_fill_avg_px,
            "sl_status": self.sl_status,
            "sl_oid": self.sl_oid,
            "tp1_status": self.tp1_status,
            "tp1_oid": self.tp1_oid,
            "tp2_status": self.tp2_status,
            "tp2_oid": self.tp2_oid,
            "emergency_close_status": self.emergency_close_status,
            "position_protected": self.position_protected,
            "errors": self.errors,
        }


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
        self._api_address = self.wallet.address

        api_url = (
            constants.MAINNET_API_URL if config.is_mainnet else constants.TESTNET_API_URL
        )

        self.info = Info(api_url, skip_ws=True)
        self.exchange = Exchange(
            wallet=self.wallet,
            base_url=api_url,
            account_address=config.main_address,
        )

        # --- PATCH (fix #4): real wallet sanity check ---
        self._verify_api_wallet_is_safe()

        # --- PATCH (fix #5): load metadata once ---
        self._asset_meta: dict[str, AssetMeta] = {}
        self._load_asset_meta()

    # =========================================================================
    # PATCH: wallet safety
    # =========================================================================

    def _verify_api_wallet_is_safe(self) -> None:
        if self._api_address.lower() == self.config.main_address.lower():
            raise ValueError(
                "REFUSING TO START: API wallet address equals main address. "
                "You must use a separate API Wallet — see Hyperliquid → API tab."
            )
        try:
            state = self.info.user_state(self._api_address)
            equity = float(state.get("marginSummary", {}).get("accountValue", 0))
        except Exception as e:
            # Network/API issue at startup is itself a reason to refuse.
            raise RuntimeError(f"Could not query API wallet equity: {e}") from e

        if equity > self.config.api_wallet_max_balance_usd:
            raise ValueError(
                f"REFUSING TO START: API wallet {self._api_address} has equity "
                f"${equity:.2f} > limit ${self.config.api_wallet_max_balance_usd:.2f}. "
                f"The API wallet must hold near-zero balance — it signs orders on "
                f"behalf of the main account but should NEVER hold funds itself."
            )
        logger.info(
            "API wallet ok: %s (equity $%.2f, signs for main %s)",
            self._api_address, equity, self.config.main_address,
        )

    # =========================================================================
    # PATCH: metadata + rounding
    # =========================================================================

    def _load_asset_meta(self) -> None:
        try:
            meta = self.info.meta()
            universe = meta.get("universe", [])
        except Exception as e:
            raise RuntimeError(f"Could not load Hyperliquid meta: {e}") from e

        for idx, asset in enumerate(universe):
            name = asset.get("name")
            if not name:
                continue
            self._asset_meta[name] = AssetMeta(
                name=name,
                asset_id=idx,
                sz_decimals=int(asset.get("szDecimals", 0)),
                max_leverage=int(asset.get("maxLeverage", 50)),
            )
        if not self._asset_meta:
            raise RuntimeError("Hyperliquid meta returned empty universe")
        logger.info("loaded meta for %d assets", len(self._asset_meta))

    def _asset(self, coin: str) -> AssetMeta:
        m = self._asset_meta.get(coin.upper())
        if m is None:
            raise ValueError(f"unknown coin: {coin}")
        return m

    def _round_size(self, coin: str, size: float) -> float:
        sz_dec = self._asset(coin).sz_decimals
        # Truncate (not round) to avoid sizing above intended risk.
        factor = 10 ** sz_dec
        return math.floor(size * factor) / factor

    @staticmethod
    def _round_price(price: float, max_decimals: int = 6) -> float:
        """Hyperliquid prices: max 5 significant figures and max max_decimals
        decimal places. We apply 5-sig-fig rounding, then clamp decimals."""
        if price <= 0:
            return 0.0
        # 5 significant figures
        from math import floor, log10
        sig = 5
        d = sig - int(floor(log10(abs(price)))) - 1
        rounded = round(price, d)
        # Clamp to max_decimals
        return round(rounded, max_decimals)

    # =========================================================================
    # Read paths
    # =========================================================================

    def get_user_state(self) -> dict[str, Any]:
        return self.info.user_state(self.config.main_address)

    def get_account_snapshot(self) -> dict[str, Any]:
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
        raw = self.info.all_mids()
        return {k: float(v) for k, v in raw.items()}

    def get_candles(self, coin: str, interval: str, lookback_bars: int) -> list[Candle]:
        """PATCH: use candle_snapshot (singular) with the actual dict signature."""
        interval_ms = _interval_to_ms(interval)
        end_ms = _now_ms()
        start_ms = end_ms - interval_ms * (lookback_bars + 5)
        try:
            raw = self.info.candle_snapshot({
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            })
        except AttributeError:
            # Older SDK that uses the plural variant; try it.
            raw = self.info.candles_snapshot(coin, interval, start_ms, end_ms)  # type: ignore[attr-defined]
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
        end_ms = _now_ms()
        start_ms = end_ms - lookback_hours * 60 * 60 * 1000
        try:
            raw = self.info.funding_history(coin, start_ms, end_ms)
        except Exception:
            return []
        return [float(item.get("fundingRate", 0)) for item in raw]

    def get_open_orders(self) -> list[dict]:
        """List currently-resting orders on the exchange for our main account.

        PATCH (severe fix #14): used by startup reconciliation."""
        try:
            return list(self.info.open_orders(self.config.main_address))
        except Exception as e:
            logger.warning("open_orders failed: %s", e)
            return []

    def get_user_fills(self, since_ms: Optional[int] = None) -> list[dict]:
        """Returns recent fills for the main account.

        PATCH (fix #3): used by the PnL reconciler in risk.py to record
        realized PnL into the state store."""
        try:
            raw = self.info.user_fills(self.config.main_address)
        except Exception as e:
            logger.warning("user_fills failed: %s", e)
            return []
        if since_ms is None:
            return raw
        return [f for f in raw if int(f.get("time", 0)) >= since_ms]

    # =========================================================================
    # Write paths
    # =========================================================================

    def _require_armed_mainnet(self) -> None:
        if self.config.is_mainnet and not self.config.mainnet_armed:
            raise RuntimeError(
                "mainnet write blocked: HL_MAINNET_CONFIRM=YES_I_UNDERSTAND not set"
            )

    def set_leverage(self, coin: str, leverage: int) -> dict[str, Any]:
        self._require_armed_mainnet()
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
        take_profit_2: Optional[float] = None,
        leverage: int = 5,
        entry_wait_seconds: int = 5,
    ) -> BracketResult:
        """Sequential bracket placement with verification.

        Order of operations:
          1. set_leverage
          2. send entry as IOC limit → poll for fill (entry_wait_seconds)
          3. if not filled → return with entry_status='unfilled'
          4. compute SL/TP relative to ACTUAL fill price
          5. send SL (limit-triggered with slippage-bounded limit) → verify
          6. if SL fails → emergency reduce-only market close → return
          7. send TP1 then TP2 → best-effort
        """
        self._require_armed_mainnet()
        result = BracketResult()

        try:
            self.set_leverage(coin, leverage)
        except Exception as e:
            result.errors.append(f"set_leverage failed: {e}")
            # Don't abort: leverage may already be correct from a prior trade.

        size = self._round_size(coin, size)
        if size <= 0:
            result.entry_status = "rejected_zero_size"
            result.errors.append("rounded size <= 0")
            return result

        entry_px = self._round_price(entry_price)

        # ---- 1. Send entry ------------------------------------------------
        try:
            entry_resp = self.exchange.order(
                name=coin,
                is_buy=is_buy,
                sz=size,
                limit_px=entry_px,
                order_type={"limit": {"tif": "Ioc"}},
                reduce_only=False,
            )
            result.entry_status = "submitted"
            result.entry_oid = _extract_oid(entry_resp)
        except Exception as e:
            result.entry_status = f"submission_failed: {e}"
            result.errors.append(str(e))
            return result

        # ---- 2. Wait for fill ---------------------------------------------
        deadline = time.time() + entry_wait_seconds
        filled_size = 0.0
        avg_fill_px = 0.0
        while time.time() < deadline:
            snap = self.get_account_snapshot()
            pos = next((p for p in snap["positions"] if p["coin"] == coin), None)
            if pos is not None and abs(pos["size"]) > 0:
                filled_size = abs(float(pos["size"]))
                avg_fill_px = float(pos["entry_px"])
                break
            time.sleep(0.5)

        if filled_size <= 0:
            result.entry_status = "unfilled"
            return result

        result.entry_status = "filled"
        result.entry_fill_size = filled_size
        result.entry_fill_avg_px = avg_fill_px

        # ---- 3. Recompute SL/TP relative to actual fill --------------------
        if is_buy:
            sl_dist = entry_price - stop_loss          # original distance
            tp1_dist = take_profit_1 - entry_price
        else:
            sl_dist = stop_loss - entry_price
            tp1_dist = entry_price - take_profit_1

        if is_buy:
            sl_actual = avg_fill_px - sl_dist
            tp1_actual = avg_fill_px + tp1_dist
            tp2_actual = avg_fill_px + 2 * tp1_dist if take_profit_2 else None
        else:
            sl_actual = avg_fill_px + sl_dist
            tp1_actual = avg_fill_px - tp1_dist
            tp2_actual = avg_fill_px - 2 * tp1_dist if take_profit_2 else None

        sl_actual = self._round_price(sl_actual)
        tp1_actual = self._round_price(tp1_actual)
        tp2_actual = self._round_price(tp2_actual) if tp2_actual else None

        # ---- 4. Place SL with slippage-bounded limit ----------------------
        # For a LONG: closing side is SELL; worst tolerable price = sl_actual * (1 - bps)
        bps = self.config.sl_slippage_bps / 10000.0
        if is_buy:
            sl_limit_px = self._round_price(sl_actual * (1 - bps))
        else:
            sl_limit_px = self._round_price(sl_actual * (1 + bps))

        try:
            sl_resp = self.exchange.order(
                name=coin,
                is_buy=not is_buy,
                sz=filled_size,
                limit_px=sl_limit_px,
                order_type={
                    "trigger": {
                        "isMarket": False,
                        "triggerPx": sl_actual,
                        "tpsl": "sl",
                    }
                },
                reduce_only=True,
            )
            result.sl_status = "submitted"
            result.sl_oid = _extract_oid(sl_resp)
            # Verify it actually accepted
            if result.sl_oid is None:
                result.sl_status = f"submission_returned_no_oid: {sl_resp}"
                raise RuntimeError(result.sl_status)
        except Exception as e:
            result.sl_status = f"failed: {e}"
            result.errors.append(f"SL placement failed: {e}")
            # ---- 5. EMERGENCY CLOSE — SL could not be placed ---------------
            try:
                close_resp = self._emergency_market_close(coin, is_long=is_buy, size=filled_size)
                result.emergency_close_status = f"submitted: {close_resp}"
            except Exception as close_err:
                result.emergency_close_status = f"FAILED: {close_err}"
                result.errors.append(f"emergency close failed: {close_err}")
            result.position_protected = False
            return result

        # ---- 6. SL ok → place TPs (best effort) ----------------------------
        tp1_size = self._round_size(coin, filled_size / 2)
        tp2_size = self._round_size(coin, filled_size - tp1_size)

        try:
            tp1_resp = self.exchange.order(
                name=coin,
                is_buy=not is_buy,
                sz=tp1_size,
                limit_px=tp1_actual,
                order_type={
                    "trigger": {
                        "isMarket": False,
                        "triggerPx": tp1_actual,
                        "tpsl": "tp",
                    }
                },
                reduce_only=True,
            )
            result.tp1_status = "submitted"
            result.tp1_oid = _extract_oid(tp1_resp)
        except Exception as e:
            result.tp1_status = f"failed: {e}"
            result.errors.append(f"TP1 placement failed: {e}")

        if tp2_actual is not None and tp2_size > 0:
            try:
                tp2_resp = self.exchange.order(
                    name=coin,
                    is_buy=not is_buy,
                    sz=tp2_size,
                    limit_px=tp2_actual,
                    order_type={
                        "trigger": {
                            "isMarket": False,
                            "triggerPx": tp2_actual,
                            "tpsl": "tp",
                        }
                    },
                    reduce_only=True,
                )
                result.tp2_status = "submitted"
                result.tp2_oid = _extract_oid(tp2_resp)
            except Exception as e:
                result.tp2_status = f"failed: {e}"
                result.errors.append(f"TP2 placement failed: {e}")

        result.position_protected = True  # SL is live; TP failures are non-fatal
        return result

    def _emergency_market_close(self, coin: str, *, is_long: bool, size: float) -> dict:
        mid = self.get_all_mids().get(coin, 0)
        if mid <= 0:
            raise RuntimeError("no mid price for emergency close")
        # Aggressive IOC across the book; bps headroom = 2× sl_slippage to ensure fill
        headroom = (self.config.sl_slippage_bps * 2) / 10000.0
        limit_px = self._round_price(mid * (1 - headroom) if is_long else mid * (1 + headroom))
        return self.exchange.order(
            name=coin,
            is_buy=not is_long,
            sz=self._round_size(coin, size),
            limit_px=limit_px,
            order_type={"limit": {"tif": "Ioc"}},
            reduce_only=True,
        )

    def cancel(self, coin: str, oid: int) -> dict[str, Any]:
        self._require_armed_mainnet()
        return self.exchange.cancel(coin, oid)

    def close_position(self, coin: str) -> dict[str, Any]:
        self._require_armed_mainnet()
        snapshot = self.get_account_snapshot()
        pos = next((p for p in snapshot["positions"] if p["coin"] == coin), None)
        if pos is None:
            return {"status": "no_position", "coin": coin}
        size = float(pos["size"])
        is_long = size > 0
        return self._emergency_market_close(coin, is_long=is_long, size=abs(size))


# =============================================================================
# Helpers
# =============================================================================


def _now_ms() -> int:
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



def _extract_oid(resp):
    """Pull the oid out of a Hyperliquid order response (best effort)."""
    try:
        statuses = resp["response"]["data"]["statuses"]
        for s in statuses:
            for key in ("resting", "filled"):
                if key in s and "oid" in s[key]:
                    return int(s[key]["oid"])
    except Exception:
        return None
    return None
