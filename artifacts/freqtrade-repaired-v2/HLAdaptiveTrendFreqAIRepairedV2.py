from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
from pandas import DataFrame

_THIS_DIR = Path(__file__).resolve().parent
_PARENT_PATH = _THIS_DIR / "HLAdaptiveTrendFreqAIRepaired.py"
_PARENT_SPEC = importlib.util.spec_from_file_location("hl_adaptive_trend_freqai_repaired_parent", _PARENT_PATH)
if _PARENT_SPEC is None or _PARENT_SPEC.loader is None:
    raise ImportError(f"Unable to load repaired strategy from {_PARENT_PATH}")
_PARENT_MODULE = importlib.util.module_from_spec(_PARENT_SPEC)
_PARENT_SPEC.loader.exec_module(_PARENT_MODULE)
HLAdaptiveTrendFreqAIRepaired = _PARENT_MODULE.HLAdaptiveTrendFreqAIRepaired


class HLAdaptiveTrendFreqAIRepairedV2(HLAdaptiveTrendFreqAIRepaired):
    """Structure-filtered repair variant: keep FreqAI, but only act on cleaner trend candles."""

    min_signal_body_pct = 0.001
    max_signal_range_atr = 1.0
    long_ext_atr_min = 1.5
    long_ext_atr_max = 4.0
    short_ext_atr_min = -4.0
    short_ext_atr_max = -1.5

    @staticmethod
    def _signal_body_pct(df: DataFrame) -> pd.Series:
        open_ = pd.to_numeric(df.get("open"), errors="coerce")
        close = pd.to_numeric(df.get("close"), errors="coerce")
        return ((close - open_) / open_.replace(0, pd.NA)).fillna(0.0)

    @staticmethod
    def _signal_range_atr(df: DataFrame) -> pd.Series:
        high = pd.to_numeric(df.get("high"), errors="coerce")
        low = pd.to_numeric(df.get("low"), errors="coerce")
        atr = pd.to_numeric(df.get("atr"), errors="coerce")
        return ((high - low) / atr.replace(0, pd.NA)).replace([float("inf"), float("-inf")], pd.NA).fillna(999.0)

    @staticmethod
    def _ema_extension_atr(df: DataFrame) -> pd.Series:
        close = pd.to_numeric(df.get("close"), errors="coerce")
        ema_l = pd.to_numeric(df.get("ema_l"), errors="coerce")
        atr = pd.to_numeric(df.get("atr"), errors="coerce")
        return ((close - ema_l) / atr.replace(0, pd.NA)).replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        factors_ok = df.get("_factors_available", pd.Series(True, index=df.index)).fillna(False)
        do_predict = df.get("do_predict", pd.Series(0, index=df.index)).fillna(0)
        predicted = df.get("&-s_close", pd.Series(float("nan"), index=df.index))
        atr = df.get("atr", pd.Series(0.0, index=df.index)).fillna(0.0)
        adx = df.get("adx", pd.Series(0.0, index=df.index)).fillna(0.0)
        funding = df.get("funding_diff_hl_bn", pd.Series(float("nan"), index=df.index))
        whale_flow = df.get("whale_net_flow_24h_usd", pd.Series(float("nan"), index=df.index))
        funding_var = df.get("_funding_factor_var", pd.Series(False, index=df.index)).fillna(False)
        whale_var = df.get("_whale_factor_var", pd.Series(False, index=df.index)).fillna(False)

        signal_body_pct = self._signal_body_pct(df)
        signal_range_atr = self._signal_range_atr(df)
        ema_extension_atr = self._ema_extension_atr(df)

        long_funding_ok = (~funding_var) | (funding < 0.0003)
        short_funding_ok = (~funding_var) | (funding > -0.0003)
        long_whale_ok = (~whale_var) | (whale_flow >= 0)
        short_whale_ok = (~whale_var) | (whale_flow <= 0)

        clean_long_structure = (
            (signal_body_pct > self.min_signal_body_pct)
            & (signal_range_atr < self.max_signal_range_atr)
            & (ema_extension_atr > self.long_ext_atr_min)
            & (ema_extension_atr < self.long_ext_atr_max)
        )
        clean_short_structure = (
            (signal_body_pct < -self.min_signal_body_pct)
            & (signal_range_atr < self.max_signal_range_atr)
            & (ema_extension_atr > self.short_ext_atr_min)
            & (ema_extension_atr < self.short_ext_atr_max)
        )

        long_cond = (
            factors_ok
            & (do_predict == 1)
            & (predicted > (df["target_roi"] + self.long_prediction_edge))
            & (df["ema_s"] > df["ema_l"])
            & (adx > float(self.buy_adx_min.value))
            & (df["close"] > (df["ema_4h_4h"] + atr * self.trend_filter_atr_ratio))
            & long_funding_ok
            & long_whale_ok
            & clean_long_structure
        )
        df.loc[long_cond, ["enter_long", "enter_tag"]] = (1, "freqai_long_repaired_v2")

        short_cond = (
            factors_ok
            & (do_predict == 1)
            & (predicted < (df["sell_roi"] - self.short_prediction_edge))
            & (df["ema_s"] < df["ema_l"])
            & (adx > float(self.buy_adx_min.value))
            & (df["close"] < (df["ema_4h_4h"] - atr * self.trend_filter_atr_ratio))
            & short_funding_ok
            & short_whale_ok
            & clean_short_structure
        )
        df.loc[short_cond, ["enter_short", "enter_tag"]] = (1, "freqai_short_repaired_v2")
        return df
