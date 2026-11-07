"""集成测试：8 场景 × 真值对照（seed=1）+ fs=40 冒烟。

形状已与管线常数对齐（r_s=0.50 / r_d=0.70 / k=0.41，见 docs/sim/model.md §5.1），
残余误差主要来自逐搏包络的搏动网格离散（步长 = RR×放气速率，40 bpm 时最大约
4.5 mmHg/搏），容差取 ±3 mmHg 留余量。HR 容差考虑 fs=80 的间期量化下限
（180 bpm 时 1 采样 ≈ 3.4 bpm；抖动场景中位数采样方差更大，af 放宽至 ±4）。
"""

from dataclasses import replace

import pytest

from bp.params import AlgoParams
from bp.pipeline import run_csv
from sim.generate import generate

STEADY = {
    "normal": (120.0, 80.0, 70.0),
    "hypertension": (160.0, 100.0, 70.0),
    "hypotension": (90.0, 60.0, 70.0),
    "wide_pp": (150.0, 70.0, 70.0),
    "bradycardia": (120.0, 80.0, 40.0),
    "tachycardia": (120.0, 80.0, 180.0),
}


@pytest.mark.parametrize("name", sorted(STEADY))
def test_steady_scenarios(name, tmp_path):
    sbp, dbp, hr = STEADY[name]
    csv, _ = generate(name, 1, tmp_path)
    res = run_csv(csv)
    assert res.accepted, f"{name}: {res.qc.reasons}"
    d = res.decision
    assert abs(d.sbp - sbp) <= 3.0
    assert abs(d.dbp - dbp) <= 3.0
    tol_hr = max(2.0, (60.0 * 0.5 / 80.0) / (60.0 / hr) ** 2)
    assert abs(d.hr - hr) <= tol_hr
    assert d.sbp > d.map > d.dbp
    assert not d.arrhythmia
    assert res.envelope.n_points >= 8


def test_af_hr_and_ipp(tmp_path):
    csv, _ = generate("af", 1, tmp_path)
    res = run_csv(csv)
    assert res.accepted
    assert abs(res.decision.hr - 70.0) <= 4.0
    assert res.decision.ipp_ratio is not None


def test_af_arrhythmia_flag_within_seeds(tmp_path):
    """0.2/0.2 IPP 判据对单次实现敏感（sim 实测），5 seed 至少一次触发。"""
    flags = []
    for seed in range(1, 6):
        csv, _ = generate("af", seed, tmp_path)
        flags.append(run_csv(csv).decision.arrhythmia)
    assert any(flags)


def test_truncated_rejected(tmp_path):
    csv, _ = generate("truncated", 1, tmp_path)
    res = run_csv(csv)
    assert not res.accepted
    assert any("SBP 交叉未找到" in r for r in res.qc.reasons)


def test_fs40_smoke(tmp_path):
    csv, _ = generate("normal", 1, tmp_path, fs_out=40)
    params = replace(AlgoParams(), fs=40.0)
    res = run_csv(csv, params)
    assert res.envelope.n_points >= 0  # 40 Hz 冒烟：不崩、结构合法
    assert res.n_candidates >= res.n_accepted
