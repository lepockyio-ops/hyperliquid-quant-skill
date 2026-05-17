from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pandas as pd
from pandas import DataFrame

_THIS_DIR = Path(__file__).resolve().parent
_BASE_STRATEGY_PATH = _THIS_DIR / "HLAdaptiveTrendFreqAI.py"
_BASE_SPEC = importlib.util.spec_from_file_location("hl_adaptive_trend_freqai_base", _BASE_STRATEGY_PATH)
if _BASE_SPEC is None or _BASE_SPEC.loader is None:
    raise ImportError(f"Unable to load base strategy from {_BASE_STRATEGY_PATH}")
_BASE_MODULE = importlib.util.module_from_spec(_BASE_SPEC)
_BASE_SPEC.loader.exec_module(_BASE_MODULE)
HLAdaptiveTrendFreqAI = _BASE_MODULE.HLAdaptiveTrendFreqAI


class HLAdaptiveTrendFreqAIRepaired(HLAdaptiveTrendFreqAI):
    """P0-P4 repair variant focused on realistic execution and factor-aware gating."""

    retracement_atr_ratio = 0.10
    exit_atr_ratio = 0.35
    leverage_hard_cap = 3.0
    stake_cap_usd = 75.0
    long_prediction_edge = 0.005
    short_prediction_edge = 0.0025
    trend_filter_atr_ratio = 0.35

    factor_roll_window = 24
    target_center_window = 48
    min_factor_variation = 1e-9

    @staticmethod
    def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
        mean = series.rolling(window, min_periods=max(3, window // 4)).mean()
        std = series.rolling(window, min_periods=max(3, window // 4)).std()
        z = (series - mean) / std.replace(0, pd.NA)
        return z.replace([pd.NA, pd.NaT, float("inf"), float("-inf")], 0.0).fillna(0.0)

    @staticmethod
    def _rolling_delta(series: pd.Series, window: int) -> pd.Series:
        baseline = series.rolling(window, min_periods=max(3, window // 4)).mean()
        return (series - baseline).fillna(0.0)

    @staticmethod
    def _variation_flag(series: pd.Series, window: int, threshold: float) -> pd.Series:
        std = series.rolling(window, min_periods=max(3, window // 4)).std().fillna(0.0)
        return std > threshold

    def feature_engineering_standard(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        dataframe = super().feature_engineering_standard(dataframe, metadata, **kwargs)

        window = self.factor_roll_window

        funding = pd.to_numeric(dataframe.get("funding_diff_hl_bn"), errors="coerce")
        basis = pd.to_numeric(dataframe.get("basis_bp"), errors="coerce")
        liq = pd.to_numeric(dataframe.get("liq_cluster_dist_pct"), errors="coerce")
        whale = pd.to_numeric(dataframe.get("whale_net_flow_24h_usd"), errors="coerce")

        if funding is not None:
            dataframe["%-funding_diff_hl_bn_z24"] = self._rolling_zscore(funding, window)
            dataframe["%-funding_diff_hl_bn_delta24"] = self._rolling_delta(funding, window)
            dataframe["_funding_factor_var"] = self._variation_flag(funding, window, self.min_factor_variation)
        if basis is not None:
            dataframe["%-basis_bp_z24"] = self._rolling_zscore(basis, window)
            dataframe["%-basis_bp_delta24"] = self._rolling_delta(basis, window)
        if liq is not None:
            dataframe["%-liq_cluster_dist_pct_z24"] = self._rolling_zscore(liq, window)
            dataframe["%-liq_cluster_dist_pct_delta24"] = self._rolling_delta(liq, window)
        if whale is not None:
            dataframe["%-whale_net_flow_24h_usd_z24"] = self._rolling_zscore(whale, window)
            dataframe["%-whale_net_flow_24h_usd_delta24"] = self._rolling_delta(whale, window)
            dataframe["_whale_factor_var"] = self._variation_flag(whale, window, self.min_factor_variation)

        return dataframe

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        label_period = self.freqai_info["feature_parameters"]["label_period_candles"]
        raw_forward = (
            dataframe["close"].shift(-label_period).rolling(label_period).mean() / dataframe["close"] - 1
        )
        centerline = raw_forward.rolling(self.target_center_window, min_periods=max(5, self.target_center_window // 4)).mean()
        dataframe["&-s_close"] = (raw_forward - centerline).fillna(raw_forward)
        return dataframe

    def custom_entry_price(
        self,
        pair: str,
        trade,
        current_time: datetime,
        proposed_rate: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        row = self._latest_signal_row(pair)
        return self._entry_retracement_price(row, proposed_rate, side)

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

        long_funding_ok = (~funding_var) | (funding < 0.0003)
        short_funding_ok = (~funding_var) | (funding > -0.0003)
        long_whale_ok = (~whale_var) | (whale_flow >= 0)
        short_whale_ok = (~whale_var) | (whale_flow <= 0)

        long_cond = (
            factors_ok
            & (do_predict == 1)
            & (predicted > (df["target_roi"] + self.long_prediction_edge))
            & (df["ema_s"] > df["ema_l"])
            & (adx > float(self.buy_adx_min.value))
            & (df["close"] > (df["ema_4h_4h"] + atr * self.trend_filter_atr_ratio))
            & long_funding_ok
            & long_whale_ok
        )
        df.loc[long_cond, ["enter_long", "enter_tag"]] = (1, "freqai_long_repaired")

        short_cond = (
            factors_ok
            & (do_predict == 1)
            & (predicted < (df["sell_roi"] - self.short_prediction_edge))
            & (df["ema_s"] < df["ema_l"])
            & (adx > float(self.buy_adx_min.value))
            & (df["close"] < (df["ema_4h_4h"] - atr * self.trend_filter_atr_ratio))
            & short_funding_ok
            & short_whale_ok
        )
        df.loc[short_cond, ["enter_short", "enter_tag"]] = (1, "freqai_short_repaired")
        return df
