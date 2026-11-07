"""算法移植四幕 demo 的可执行验收（Agg + tmp_path）。"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

FIRMWARE_DIR = Path(__file__).resolve().parents[1]
if str(FIRMWARE_DIR) not in sys.path:
    sys.path.insert(0, str(FIRMWARE_DIR))

from demo import run_algo_demo  # noqa: E402


def test_act1_coverify_table():
    result = run_algo_demo.act1_coverify_table()
    assert result["passed"] is True
    assert len(result["rows"]) == 3
    assert {row["name"] for row in result["rows"]} == {
        "normal",
        "hypertension",
        "hypotension",
    }
    for row in result["rows"]:
        assert row["passed"] is True
        assert abs(row["d_sbp"]) <= 1.0
        assert abs(row["d_dbp"]) <= 1.0
        assert abs(row["d_hr"]) <= 1.0


def test_act2_envelope_plots(tmp_path):
    result = run_algo_demo.act2_envelope_plots(tmp_path)
    assert result["passed"] is True
    assert len(result["plots"]) == 3
    for path in result["plots"]:
        plot = Path(path)
        assert plot.exists()
        assert plot.stat().st_size > 0
        assert plot.name.startswith("algo_envelope_")
        assert plot.suffix == ".png"
    for numeric in result["numerics"]:
        assert numeric["max_pc_diff"] <= 1e-3
        assert numeric["max_amp_diff"] <= 0.05


def test_act3_fsm_end_to_end():
    result = run_algo_demo.act3_fsm_end_to_end()
    assert result["passed"] is True
    assert result["steps"] > 0
    assert result["buffer_samples"] > 1000
    firmware = result["firmware"]
    pc = result["pc"]
    assert firmware["accepted"] is True
    assert pc["accepted"] is True
    assert firmware["qc_reasons"] == pc["qc_reasons"]
    assert abs(firmware["sbp"] - pc["sbp"]) <= 0.5
    assert abs(firmware["dbp"] - pc["dbp"]) <= 0.5
    assert abs(firmware["hr"] - pc["hr"]) <= 0.5


def test_act4_budget_and_error_overview(tmp_path):
    result = run_algo_demo.act4_budget_and_error_overview(tmp_path)
    assert result["passed"] is True
    memory = result["memory"]
    assert memory["total_kib"] <= memory["budget_kib"]
    assert memory["total_kib"] <= memory["target_kib"]
    assert memory["buffer_kib"] > 0
    assert memory["analyzer_kib"] > 0
    assert all(value <= 250.0 for value in result["timing"]["x100_ms"])
    assert len(result["f32"]) == 3
    for row in result["f32"]:
        assert row["d_sbp"] <= 1.0
        assert row["d_dbp"] <= 1.0
        assert row["d_hr"] <= 1.0
    plot = Path(result["plot"])
    assert plot.exists()
    assert plot.stat().st_size > 0
    assert plot.name == "algo_error_overview.png"
