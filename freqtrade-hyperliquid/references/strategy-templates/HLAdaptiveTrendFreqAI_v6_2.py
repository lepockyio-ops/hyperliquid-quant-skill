"""HLAdaptiveTrendFreqAI_v6_2 - scored-vote frequency rebalance template.

Reference template for the v6.2 fix described in
`freqtrade-hyperliquid/references/v6_2-scored-vote-frequency-rebalance.md`.

This file is shipped as a REFERENCE TEMPLATE inside the skill bundle. Copy
it into your Freqtrade `user_data/strategies/` directory, reconcile with
the V6.1 production code (factor merge logic, FreqAI hooks, etc.), then
backtest under the same timerange used for V6.1 before promoting.

Design summary
--------------
- 5m timeframe, 1-5h target hold, 30-60 trades/month target.
- Entry surface = MUST conditions (2-4 hard gates) + scored vote.
- VOTE_THRESHOLD: 3 of 6 for `long_trend` / `long_direct`; 4 of 6 for
  `long_pullback` / all `short_*` modes (long bias from V6 attribution).
- ATR multiplier 1.5 (5m-appropriate; v3-v7 default 2.5 was 1h-tuned).
- Partial TP ladder via `adjust_trade_position`.
- Hold-time guard via `custom_exit` to enforce 1-5h target.
- Candle-time freshness check (no wall-clock during backtest).
- Post-only `time_in_force = "PO"` entries to compress fee surface.
"""
from __future__ import annotations

import pathlib
from typing import Any

import pandas as pd
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib  # noqa: F401  (kept for parity with V6.1)
from freqtrade.enums import RunMode
from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IntParameter,
    IStrategy,
    merge_informative_pair,
)

VIBE_PARQUET_DIR = pathlib.Path("/freqtrade/user_data/external_factors")

VIBE_FACTOR_MAX_AGE = {
    "funding_basis": pd.Timedelta(minutes=15),
    "liquidations":  pd.Timedelta(minutes=10),
    "onchain":       pd.Timedelta(minutes=30),
}
VIBE_REQUIRED_FIELDS = {
    "funding_basis": ["funding_diff_hl_bn", "basis_bp"],
    "liquidations":  ["liq_cluster_dist_pct"],
    "onchain":       ["whale_net_flow_24h_usd"],
}


class HLAdaptiveTrendFreqAI_v6_2(IStrategy):
    """V6.2 scored-vote rebalance of the V6.1 frozen variant."""

    INTERFACE_VERSION = 3
    timeframe = "5m"
    informative_timeframes = ["1h", "4h"]
    can_short = True
    use_custom_stoploss = True
    process_only_new_candles = True
    position_adjustment_enable = True
    max_entry_position_adjustment = 0  # exit-side adjustments only
    startup_candle_count: int = 200

    minimal_roi = {"0": 100}
    stoploss = -0.05
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "limit",
        "stoploss_on_exchange": True,
        "stoploss_on_exchange_interval": 60,
        "stoploss_on_exchange_limit_ratio": 0.99,
    }
    order_time_in_force = {"entry": "PO", "exit": "GTC"}

    buy_ema_short = IntParameter(5, 15, default=8, space="buy")
    buy_ema_long  = IntParameter(20, 50, default=30, space="buy")
    buy_adx_floor = DecimalParameter(15.0, 25.0, default=18.0, decimals=1, space="buy")
    sell_atr_mult = DecimalParameter(1.0, 2.5, default=1.5, decimals=1, space="sell")
    vote_threshold_standard = IntParameter(2, 4, default=3, space="buy")
    vote_threshold_strict   = IntParameter(3, 5, default=4, space="buy")

    # ---- Helpers ----
    def _now_for_freshness(self, df: pd.DataFrame) -> pd.Timestamp:
        if self.dp is not None and self.dp.runmode in (RunMode.LIVE, RunMode.DRY_RUN):
            return pd.Timestamp.utcnow().tz_localize(None)
        if df.empty:
            return pd.Timestamp.utcnow().tz_localize(None)
        ts = df["date"].iloc[-1]
        return ts.tz_localize(None) if ts.tzinfo is not None else ts

    def _merge_vibe_factors(self, df: pd.DataFrame, pair: str) -> pd.DataFrame:
        df["_factors_available"] = True
        df["_factors_block_reason"] = ""

        now = self._now_for_freshness(df)
        for name in ("funding_basis", "liquidations", "onchain"):
            path = VIBE_PARQUET_DIR / f"{name}.parquet"
            max_age = VIBE_FACTOR_MAX_AGE[name]
            required = VIBE_REQUIRED_FIELDS[name]
            if not path.exists():
                df["_factors_available"] = False
                df["_factors_block_reason"] = f"missing_file:{name}"
                continue
            tbl = pd.read_parquet(path)
            tbl = tbl[tbl["pair"] == pair].sort_values("as_of")
            if tbl.empty:
                df["_factors_available"] = False
                df["_factors_block_reason"] = f"empty_table:{name}"
                continue
            latest = pd.to_datetime(tbl["as_of"].iloc[-1])
            if latest.tzinfo is not None:
                latest = latest.tz_localize(None)
            if now - latest > max_age:
                df["_factors_available"] = False
                df["_factors_block_reason"] = f"stale:{name}"
            df = pd.merge_asof(
                df.sort_values("date"),
                tbl, left_on="date", right_on="as_of",
                direction="backward", tolerance=max_age,
            )
            for col in required:
                if col not in df.columns or pd.isna(df[col].iloc[-1]):
                    df["_factors_available"] = False
                    df["_factors_block_reason"] = f"missing_field:{name}.{col}"
        return df

    def _atr_pct(self, df: pd.DataFrame) -> pd.Series:
        return df["atr"] / df["close"].replace(0, pd.NA)

    def _latest_atr(self, pair: str):
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty:
            return None
        return float(df["atr"].iloc[-1])

    # ---- Indicator pipeline ----
    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(p, "1h") for p in pairs] + [(p, "4h") for p in pairs]

    def populate_indicators(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        df["ema_s"] = ta.EMA(df, timeperiod=int(self.buy_ema_short.value))
        df["ema_l"] = ta.EMA(df, timeperiod=int(self.buy_ema_long.value))
        df["adx"]   = ta.ADX(df)
        df["atr"]   = ta.ATR(df, timeperiod=14)

        for tf in ("1h", "4h"):
            inf = self.dp.get_pair_dataframe(metadata["pair"], tf)
            inf[f"ema_{tf}"] = ta.EMA(inf, timeperiod=20)
            df = merge_informative_pair(df, inf, self.timeframe, tf, ffill=True)

        df = self._merge_vibe_factors(df, metadata["pair"])

        atr_pct = self._atr_pct(df)
        df["_v_ema_state"] = (df["ema_s"] > df["ema_l"]).astype(int)
        df["_v_above_ema"] = (df["close"] > df["ema_s"]).astype(int)
        df["_v_atr_calm"]  = (atr_pct < atr_pct.rolling(100).quantile(0.6)).astype(int)
        df["_v_funding"]   = (
            df.get("funding_diff_hl_bn", pd.Series(0.0, index=df.index))
              .abs()
              .lt(0.0005)
              .astype(int)
        )
        df["_v_freqai"] = (
            (df.get("&-direction", pd.Series(0, index=df.index)) == 1).astype(int)
            if "&-direction" in df.columns
            else pd.Series(0, index=df.index)
        )
        df["_v_liq_far"] = (
            df.get("liq_cluster_dist_pct", pd.Series(0.0, index=df.index))
              .abs()
              .gt(0.005)
              .astype(int)
        )

        df["_score_long"] = (
            df["_v_ema_state"]
            + df["_v_above_ema"]
            + df["_v_atr_calm"]
            + df["_v_funding"]
            + df["_v_freqai"]
            + df["_v_liq_far"]
        )
        df["_score_short"] = (
            (1 - df["_v_ema_state"])
            + (1 - df["_v_above_ema"])
            + df["_v_atr_calm"]
            + df["_v_funding"]
            + (df.get("&-direction", pd.Series(0, index=df.index)) == -1).astype(int)
            + df["_v_liq_far"]
        )
        return df

    # ---- Entry surface ----
    def populate_entry_trend(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        adx_floor = float(self.buy_adx_floor.value)
        vt_std = int(self.vote_threshold_standard.value)
        vt_strict = int(self.vote_threshold_strict.value)

        must_long = (
            df["_factors_available"]
            & (df["adx"] > adx_floor)
            & (df["close"] > df["ema_4h_4h"])
        )
        must_short = (
            df["_factors_available"]
            & (df["adx"] > adx_floor)
            & (df["close"] < df["ema_4h_4h"])
        )

        long_direct   = must_long & (df["_score_long"] >= vt_std)
        long_trend    = must_long & (df["_score_long"] >= vt_std) & (df["close"] > df["ema_1h_1h"])
        long_pullback = must_long & (df["_score_long"] >= vt_strict) & (df["close"] < df["ema_s"])

        df.loc[long_direct,   ["enter_long", "enter_tag"]] = (1, "freqai_long_direct_v6_2")
        df.loc[long_trend,    ["enter_long", "enter_tag"]] = (1, "freqai_long_trend_v6_2")
        df.loc[long_pullback, ["enter_long", "enter_tag"]] = (1, "freqai_long_pullback_v6_2")

        short_direct   = must_short & (df["_score_short"] >= vt_strict)
        short_trend    = must_short & (df["_score_short"] >= vt_strict) & (df["close"] < df["ema_1h_1h"])
        short_pullback = must_short & (df["_score_short"] >= vt_strict + 1) & (df["close"] > df["ema_s"])

        df.loc[short_direct,   ["enter_short", "enter_tag"]] = (1, "freqai_short_direct_v6_2")
        df.loc[short_trend,    ["enter_short", "enter_tag"]] = (1, "freqai_short_trend_v6_2")
        df.loc[short_pullback, ["enter_short", "enter_tag"]] = (1, "freqai_short_pullback_v6_2")
        return df

    def populate_exit_trend(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        df.loc[df["ema_s"] < df["ema_l"], "exit_long"] = 1
        df.loc[df["ema_s"] > df["ema_l"], "exit_short"] = 1
        return df

    # ---- Sizing ----
    def custom_stake_amount(
        self, pair: str, current_time, current_rate: float,
        proposed_stake: float, min_stake: float | None, max_stake: float,
        entry_tag: str | None, side: str, **kwargs: Any,
    ) -> float:
        equity = self.wallets.get_total_stake_amount()
        risk_pct = 0.005
        atr = self._latest_atr(pair) or current_rate * 0.01
        stop_distance = atr * float(self.sell_atr_mult.value)
        size = (equity * risk_pct) / max(stop_distance, current_rate * 0.0005)
        notional = size * current_rate
        leverage_cap = 5.0
        notional = min(notional, equity * leverage_cap)
        return max(min(notional, max_stake or notional), (min_stake or 0))

    def custom_stoploss(
        self, pair: str, trade: Trade, current_time, current_rate: float,
        current_profit: float, **kwargs: Any,
    ) -> float:
        atr = self._latest_atr(pair)
        if atr is None or trade.open_rate == 0:
            return -0.05
        sl_dist_pct = (atr * float(self.sell_atr_mult.value)) / trade.open_rate
        new_sl = -sl_dist_pct if not trade.is_short else sl_dist_pct
        if trade.stop_loss_pct is None:
            return new_sl
        return max(new_sl, trade.stop_loss_pct) if not trade.is_short else min(new_sl, trade.stop_loss_pct)

    def adjust_trade_position(
        self, trade: Trade, current_time, current_rate: float,
        current_profit: float, **kwargs: Any,
    ) -> float | None:
        if trade.open_rate == 0:
            return None
        atr = self._latest_atr(trade.pair)
        if atr is None:
            return None
        r_unit = (atr * float(self.sell_atr_mult.value)) / trade.open_rate

        if current_profit >= r_unit and not getattr(trade, "_v6_2_p1", False):
            trade._v6_2_p1 = True  # type: ignore[attr-defined]
            return -trade.amount * 0.5
        if current_profit >= 1.5 * r_unit and not getattr(trade, "_v6_2_p2", False):
            trade._v6_2_p2 = True  # type: ignore[attr-defined]
            return -trade.amount * 0.3
        return None

    def custom_exit(
        self, pair: str, trade: Trade, current_time, current_rate: float,
        current_profit: float, **kwargs: Any,
    ) -> str | None:
        hold_hours = (current_time - trade.open_date_utc).total_seconds() / 3600.0
        if hold_hours > 5.0 and current_profit < 0.003:
            return "time_exit_unproductive"
        if hold_hours > 8.0:
            return "time_exit_hard_cap"
        return None

    def confirm_trade_entry(
        self, pair: str, order_type: str, amount: float, rate: float,
        time_in_force: str, current_time, entry_tag: str | None, side: str,
        **kwargs: Any,
    ) -> bool:
        return True
