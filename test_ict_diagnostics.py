import pandas as pd

from analytics import ict_diagnostics as diag


def _redirect_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(diag, "BLOCKER_REPORT", tmp_path / "ict_blocker_report.csv")
    monkeypatch.setattr(diag, "BLOCKER_SUMMARY", tmp_path / "ict_blocker_summary.json")
    monkeypatch.setattr(diag, "NEAR_MISS_REPORT", tmp_path / "ict_near_miss_setups.csv")
    monkeypatch.setattr(diag, "FVG_RETEST_AUDIT", tmp_path / "fvg_retest_audit.csv")
    monkeypatch.setattr(diag, "LIQUIDITY_SWEEP_AUDIT", tmp_path / "liquidity_sweep_audit.csv")
    monkeypatch.setattr(diag, "STRUCTURE_AUDIT", tmp_path / "structure_audit.csv")
    diag.ensure_report_files()


def _candles():
    rows = [
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
    ]
    return pd.DataFrame([
        {"time": i, "open": o, "high": h, "low": l, "close": c}
        for i, (o, h, l, c) in enumerate(rows)
    ])


def test_blocker_summary_generation(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    signal = {
        "timestamp": "t",
        "symbol": "EURUSD",
        "action": "BUY",
        "entry": 1.1000,
        "sl": 1.0990,
        "tp": 1.1020,
        "setup_score": {"score": 0.9, "spread": {"safe": True, "spread_pips": 0.2}},
        "ict": {"session_name": "London", "components": {"htf_bias_agrees": True, "liquidity_sweep_detected": False, "bos_detected": True, "choch_detected": False, "fvg_present": True, "fvg_retest_detected": True, "order_block_valid": True}},
    }
    diag.record_blocker(signal, {"conviction": 0.8}, 5)
    summary = diag.build_summary()
    assert summary["total_scanned_setups"] == 1
    assert summary["total_blocked_setups"] == 1
    assert summary["most_common_blocker"][0] == "liquidity_sweep"


def test_near_miss_detection(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    signal = {
        "timestamp": "t",
        "symbol": "EURUSD",
        "action": "BUY",
        "entry": 1.1000,
        "sl": 1.0990,
        "tp": 1.1020,
        "setup_score": {"score": 0.9, "spread": {"safe": True, "spread_pips": 0.2}},
        "ict": {"session_name": "London", "components": {"htf_bias_agrees": True, "liquidity_sweep_detected": True, "bos_detected": False, "choch_detected": False, "fvg_present": True, "fvg_retest_detected": True, "order_block_valid": True}},
    }
    diag.record_blocker(signal, {"conviction": 0.8}, 5)
    rows = diag._read_csv(diag.NEAR_MISS_REPORT)
    assert len(rows) == 1
    assert rows[0]["failed_reasons"] == "bos_or_choch"


def test_fvg_retest_audit(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ICT_MIN_FVG_PIPS_FX", "0.5")
    diag.audit_detectors(_candles(), "EURUSD", "BUY", 5)
    rows = diag._read_csv(diag.FVG_RETEST_AUDIT)
    assert rows
    assert "whether_price_returned" in rows[0]


def test_liquidity_sweep_audit(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    diag.audit_detectors(_candles(), "EURUSD", "BUY", 5)
    rows = diag._read_csv(diag.LIQUIDITY_SWEEP_AUDIT)
    assert rows
    assert rows[0]["valid_or_invalid"] in ["valid", "invalid"]


def test_structure_audit(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    candles = _candles()
    diag.audit_detectors(candles, "EURUSD", "BUY", 5)
    assert diag.STRUCTURE_AUDIT.exists()
