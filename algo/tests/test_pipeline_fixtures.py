"""fixtures 回归：生成数据 → 完整管线 → 期望范围（seed 固定，结果确定）。"""

import numpy as np

from bp.csvio import Recording
from bp.pipeline import run, run_csv
from sim.generate import generate


def test_normal_fixture_end_to_end(tmp_path):
    csv, _ = generate("normal", 1, tmp_path)
    res = run_csv(csv)
    assert res.accepted and res.qc.ok
    d = res.decision
    assert abs(d.sbp - 120.0) <= 8.0
    assert abs(d.dbp - 80.0) <= 8.0
    assert abs(d.hr - 70.0) <= 2.0
    assert abs(d.map - 100.0) <= 5.0
    assert res.envelope.n_points >= 8
    assert not d.arrhythmia and not d.dia_flag
    assert d.sbp > d.map > d.dbp
    assert res.n_candidates == res.n_accepted  # 干净数据无剔除


def test_truncated_fixture_rejected(tmp_path):
    csv, _ = generate("truncated", 1, tmp_path)
    res = run_csv(csv)
    assert not res.accepted
    assert any("SBP 交叉未找到" in r for r in res.qc.reasons)


def test_no_deflate_segment_rejected():
    n = 200
    rec = Recording(
        t_ms=np.arange(n) * 12.5,
        p_raw=np.full(n, 100.0),
        phase=np.array(["INFLATE"] * n),
    )
    res = run(rec)
    assert not res.accepted
    assert "无 DEFLATE 段" in res.qc.reasons


def test_short_deflate_segment_rejected():
    n = 100
    rec = Recording(
        t_ms=np.arange(n) * 12.5,
        p_raw=np.full(n, 100.0),
        phase=np.array(["DEFLATE"] * n),
    )
    res = run(rec)
    assert not res.accepted
    assert "DEFLATE 段过短" in res.qc.reasons
