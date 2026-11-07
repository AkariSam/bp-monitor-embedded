"""会话式固件分析器：多步推进、结果字段、QC 拒绝、与 PC 同缓冲对拍。"""

import numpy as np

from analyzer import PipelineAnalyzer
from bp.csvio import Recording
from bp.pipeline import run as pc_pipeline_run
from buffer import DeflateBuffer


def _run(buffer):
    analyser = PipelineAnalyzer()
    analyser.start(buffer)
    result = None
    steps = 0
    while result is None and steps < 30:
        result = analyser.step()
        steps += 1
    return analyser, result, steps


def _pc_reference(buffer):
    n = len(buffer)
    t_ms = []
    p_raw = []
    for _seq, t, p in buffer:
        t_ms.append(float(t))
        p_raw.append(p)
    rec = Recording(
        t_ms=np.array(t_ms, dtype=float),
        p_raw=np.array(p_raw, dtype=float),
        phase=np.array(["DEFLATE"] * n),
    )
    return pc_pipeline_run(rec)


def test_normal_buffer_multistep_and_fields(pc_normal_case, make_deflate_buffer):
    rec, _res = pc_normal_case
    buffer = make_deflate_buffer(rec)
    analyser, result, steps = _run(buffer)

    assert result is not None
    assert steps > 1
    assert analyser.start_calls == 1
    assert analyser.step_calls == steps
    assert analyser.step() is result

    assert result["result"] == "OK"
    assert result["reason"] == "NONE"
    data = result["data"]
    for key in (
        "sbp",
        "dbp",
        "map",
        "hr",
        "dia_delta",
        "dia_flag",
        "ipp_ratio",
        "arrhythmia",
        "hr_flag",
        "n_candidates",
        "n_accepted",
        "n_points",
        "n_samples",
        "env_cv",
        "outlier_ratio",
        "qc_ok",
        "qc_reasons",
        "fs",
    ):
        assert key in data, key
    assert isinstance(data["sbp"], (int, float))
    assert isinstance(data["dbp"], (int, float))
    assert isinstance(data["map"], (int, float))
    assert isinstance(data["hr"], (int, float))
    assert data["dbp"] < data["map"] < data["sbp"]
    assert data["n_samples"] == len(buffer)
    assert data["n_points"] >= 8
    assert data["qc_ok"] is True
    assert data["qc_reasons"] == []

    pc_result = _pc_reference(buffer)
    assert pc_result.accepted
    assert abs(data["sbp"] - pc_result.decision.sbp) <= 0.5
    assert abs(data["dbp"] - pc_result.decision.dbp) <= 0.5


def test_truncated_buffer_rejects_with_pc_reason(
    pc_case_loader, make_deflate_buffer
):
    rec, _res = pc_case_loader("truncated_s001")
    buffer = make_deflate_buffer(rec)
    analyser, result, _steps = _run(buffer)

    assert result["result"] == "REJECT"
    assert "SBP 交叉未找到" in result["reason"]
    assert result["data"]["qc_ok"] is False
    assert result["data"]["sbp"] is None

    pc_result = _pc_reference(buffer)
    assert not pc_result.accepted
    assert any("SBP 交叉未找到" in reason for reason in pc_result.qc.reasons)


def test_short_and_empty_buffers_reject_quickly():
    short = DeflateBuffer(10)
    for i in range(10):
        short.append(i, i * 12, 100.0)
    _analyser, result, steps = _run(short)
    assert steps == 1
    assert result["result"] == "REJECT"
    assert result["reason"] == "DEFLATE 段过短"

    empty = DeflateBuffer(4)
    _analyser, result, steps = _run(empty)
    assert steps == 1
    assert result["result"] == "REJECT"
    assert result["reason"] == "无 DEFLATE 段"
