import pandas as pd

from strategy.ict import (
    analyze_ict,
    detect_fvg_retest,
    detect_liquidity,
    detect_market_structure,
    detect_order_block,
)


def _df(rows):
    return pd.DataFrame([
        {"time": i, "open": o, "high": h, "low": l, "close": c}
        for i, (o, h, l, c) in enumerate(rows)
    ])


def test_liquidity_sweep_detects_sell_side_reclaim():
    candles = _df([
        (1.1000, 1.1010, 1.0990, 1.1005),
        (1.1005, 1.1012, 1.0992, 1.1007),
        (1.1007, 1.1011, 1.0991, 1.1004),
        (1.1004, 1.1013, 1.0993, 1.1008),
        (1.1008, 1.1012, 1.0992, 1.1006),
        (1.1006, 1.1014, 1.0994, 1.1009),
        (1.1009, 1.1011, 1.0991, 1.1005),
        (1.1005, 1.1012, 1.0992, 1.1007),
        (1.1007, 1.1013, 1.0993, 1.1006),
        (1.1006, 1.1012, 1.0992, 1.1008),
        (1.1008, 1.1011, 1.0991, 1.1005),
        (1.1005, 1.1009, 1.0984, 1.0998),
    ])
    liquidity = detect_liquidity(candles, "EURUSD")
    assert liquidity["liquidity_sweep_detected"] is True
    assert liquidity["sweep"]["direction"] == "Bullish"


def test_fvg_retest_requires_gap_and_return_to_zone(monkeypatch):
    monkeypatch.setenv("ICT_MIN_FVG_PIPS_FX", "0.5")
    candles = _df([
        (1.1000, 1.1010, 1.0990, 1.1005),
        (1.1005, 1.1015, 1.1002, 1.1012),
        (1.1025, 1.1035, 1.1020, 1.1030),
        (1.1030, 1.1040, 1.1012, 1.1018),
    ])
    retest = detect_fvg_retest(candles, "EURUSD", "Bullish")
    assert retest is not None
    assert retest["direction"] == "Bullish"


def test_order_block_requires_structure_and_displacement(monkeypatch):
    monkeypatch.setenv("ICT_MIN_DISPLACEMENT_BODY_RATIO", "1.2")
    candles = _df([
        (1.1000, 1.1010, 1.0990, 1.1005),
        (1.1005, 1.1015, 1.0995, 1.1010),
        (1.1010, 1.1020, 1.1000, 1.1015),
        (1.1015, 1.1025, 1.1008, 1.1010),
        (1.1010, 1.1020, 1.1005, 1.1012),
        (1.1012, 1.1030, 1.1008, 1.1026),
        (1.1026, 1.1032, 1.1018, 1.1020),
        (1.1020, 1.1048, 1.1018, 1.1046),
    ])
    structure = detect_market_structure(candles)
    structure["bos"] = {"direction": "Bullish", "level": 1.1032, "type": "BOS"}
    ob = detect_order_block(candles, structure, "EURUSD")
    assert ob is not None
    assert ob["type"] == "BUY"


def test_analyze_ict_components_align_for_buy(monkeypatch):
    monkeypatch.setenv("ICT_MIN_FVG_PIPS_FX", "0.5")
    candles = _df([
        (1.1000, 1.1010, 1.0990, 1.1005),
        (1.1005, 1.1012, 1.0992, 1.1007),
        (1.1007, 1.1011, 1.0991, 1.1004),
        (1.1004, 1.1013, 1.0993, 1.1008),
        (1.1008, 1.1012, 1.0992, 1.1006),
        (1.1006, 1.1014, 1.0994, 1.1009),
        (1.1009, 1.1011, 1.0991, 1.1005),
        (1.1005, 1.1009, 1.0984, 1.0998),
        (1.0998, 1.1005, 1.0994, 1.1002),
        (1.1002, 1.1010, 1.0999, 1.1008),
        (1.1020, 1.1035, 1.1018, 1.1030),
        (1.1030, 1.1032, 1.1010, 1.1016),
    ])
    result = analyze_ict(candles, "EURUSD", "BUY", {"direction": "Bullish"})
    assert result["components"]["htf_bias_agrees"] is True
    assert result["components"]["liquidity_sweep_detected"] is True
    assert result["components"]["fvg_retest_detected"] is True
