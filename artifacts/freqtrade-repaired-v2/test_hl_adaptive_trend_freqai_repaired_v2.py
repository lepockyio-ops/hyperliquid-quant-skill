from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


STRATEGY_PATH = Path("/home/ubuntu/quant-stack/user_data/strategies/HLAdaptiveTrendFreqAIRepairedV2.py")


def load_strategy_module():
    spec = importlib.util.spec_from_file_location("hl_adaptive_trend_freqai_repaired_v2", STRATEGY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_entry_df(**overrides) -> pd.DataFrame:
    base = {
        "_factors_available": [True],
        "do_predict": [1],
        "&-s_close": [0.04],
        "target_roi": [0.02],
        "sell_roi": [-0.02],
        "open": [100.0],
        "high": [102.0],
        "low": [100.5],
        "close": [101.2],
        "ema_4h_4h": [100.0],
        "ema_s": [101.0],
        "ema_l": [100.0],
        "adx": [28.0],
        "atr": [1.0],
        "funding_diff_hl_bn": [0.0001],
        "whale_net_flow_24h_usd": [1_000_000.0],
        "_funding_factor_var": [False],
        "_whale_factor_var": [False],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_populate_entry_trend_requires_clean_long_structure():
    module = load_strategy_module()
    strategy = module.HLAdaptiveTrendFreqAIRepairedV2(config={})

    blocked = make_entry_df(high=[103.0], low=[99.5], close=[100.8])
    result = strategy.populate_entry_trend(blocked.copy(), metadata={"pair": "ETH/USDC:USDC"})
    assert result.get("enter_long") is None or result["enter_long"].fillna(0).iloc[0] != 1

    allowed = make_entry_df(open=[100.5], high=[102.0], low=[101.1], close=[101.8])
    result = strategy.populate_entry_trend(allowed.copy(), metadata={"pair": "ETH/USDC:USDC"})
    assert result.loc[0, "enter_long"] == 1
    assert result.loc[0, "enter_tag"] == "freqai_long_repaired_v2"


def test_populate_entry_trend_requires_constructive_short_structure():
    module = load_strategy_module()
    strategy = module.HLAdaptiveTrendFreqAIRepairedV2(config={})

    short_df = make_entry_df(
        **{
            "&-s_close": [-0.04],
            "target_roi": [0.02],
            "sell_roi": [-0.02],
            "open": [100.0],
            "high": [98.9],
            "low": [98.0],
            "close": [98.4],
            "ema_4h_4h": [100.0],
            "ema_s": [99.0],
            "ema_l": [100.0],
            "funding_diff_hl_bn": [0.0001],
            "whale_net_flow_24h_usd": [1_000_000.0],
            "_funding_factor_var": [False],
            "_whale_factor_var": [False],
        }
    )
    result = strategy.populate_entry_trend(short_df.copy(), metadata={"pair": "ETH/USDC:USDC"})
    assert result.loc[0, "enter_short"] == 1
    assert result.loc[0, "enter_tag"] == "freqai_short_repaired_v2"

    blocked = short_df.copy()
    blocked["close"] = [99.9]
    blocked["high"] = [100.6]
    blocked["low"] = [98.7]
    result = strategy.populate_entry_trend(blocked.copy(), metadata={"pair": "ETH/USDC:USDC"})
    assert result.get("enter_short") is None or result["enter_short"].fillna(0).iloc[0] != 1
