"""判定（MAP/SBP/DBP/HR/DIA 校验）的单元测试。"""

import numpy as np

from bp.decision import decide
from bp.envelope import build


def _bell(map_=100.0, w=15.0, pc_lo=60.0, pc_hi=150.0, step=2.0):
    pc = np.arange(pc_lo, pc_hi + step / 2, step)
    amp = np.exp(-((pc - map_) / w) ** 2)
    return build(pc, amp)


def test_analytic_bell_crossings():
    env = _bell()
    d = decide(env, [])
    assert abs(d.map - 100.0) < 0.5
    exp_sbp = 100.0 + 15.0 * np.sqrt(np.log(2.0))
    exp_dbp = 100.0 - 15.0 * np.sqrt(np.log(1.0 / 0.7))
    assert abs(d.sbp - exp_sbp) < 0.7
    assert abs(d.dbp - exp_dbp) < 0.7
    assert not d.dia_flag


def test_dia_crosscheck_flag():
    pc = np.arange(60.0, 151.0, 2.0)
    amp = np.where(pc >= 100.0,
                   np.exp(-((pc - 100.0) / 15.0) ** 2),
                   np.exp(-((pc - 100.0) / 30.0) ** 2))
    d = decide(build(pc, amp), [])
    assert d.dia_flag  # 低压侧过宽 → 公式法偏差 >10 mmHg


def test_hr_from_peak_times():
    rr = 60.0 / 70.0
    times = np.cumsum([0.0] + [rr] * 20)
    d = decide(_bell(), times)
    assert abs(d.hr - 70.0) < 0.5
    assert not d.hr_flag
    assert not d.arrhythmia


def test_hr_flag_out_of_window():
    rr = 60.0 / 30.0
    d = decide(_bell(), np.cumsum([0.0] + [rr] * 10))
    assert d.hr_flag


def test_arrhythmia_flag_on_jitter():
    rr = 60.0 / 70.0
    times = [0.0]
    for i in range(20):
        times.append(times[-1] + rr * (1.3 if i % 2 else 0.85))
    d = decide(_bell(), times)
    assert d.ipp_ratio is not None and d.ipp_ratio > 0.2
    assert d.arrhythmia


def test_truncated_envelope_missing_sbp():
    env = _bell(pc_lo=60.0, pc_hi=110.0)  # 高压侧在 0.5 比率前截断
    d = decide(env, [])
    assert d.sbp is None
    assert "SBP 交叉未找到" in d.reasons
    assert d.dbp is not None


def test_map_at_boundary_no_crash():
    d = decide(build([100.0, 101.0, 102.0], [3.0, 2.0, 1.0]), [])
    assert d.map == 100.0
    assert d.hr is None


def test_empty_envelope():
    d = decide(build([], []), [])
    assert d.map is None and d.sbp is None and d.dbp is None
    assert "包络为空" in d.reasons