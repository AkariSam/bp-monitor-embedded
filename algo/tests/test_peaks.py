"""峰检测与峰有效性的单元测试。"""

import numpy as np

from bp.params import AlgoParams
from bp.peaks import _apply_validity, _gradient_track, accepted_peaks, detect

FS = 80.0


def _train(n=1600, rr=0.85, amps=None, width=0.08, phase0=0.4, dicrotic=0.0,
           extra=None):
    """合成搏动串：单高斯峰（可加 dicrotic 副波/额外杂峰）。"""
    t = np.arange(n) / FS
    x = np.zeros(n)
    times = np.arange(phase0, t[-1] - 0.1, rr)
    if amps is None:
        amps = [2.0] * len(times)
    for bt, amp in zip(times, amps):
        x += amp * np.exp(-0.5 * ((t - bt) / width) ** 2)
        if dicrotic:
            x += dicrotic * np.exp(-0.5 * ((t - bt - 0.15) / width) ** 2)
    if extra:
        for bt, amp in extra:
            x += amp * np.exp(-0.5 * ((t - bt) / width) ** 2)
    return t, x, times


def test_pulse_train_detected():
    t, x, times = _train()
    acc = accepted_peaks(detect(x, t))
    assert len(acc) == len(times)
    for a, bt in zip(acc, times):
        assert abs(a.t_s - bt) < 0.04
        assert abs(a.prom - 2.0) < 0.3


def test_dicrotic_lobe_rejected():
    t, x, times = _train(dicrotic=0.5)
    acc = accepted_peaks(detect(x, t))
    assert len(acc) == len(times)
    for a, bt in zip(acc, times):
        assert abs(a.t_s - bt) < 0.04


def test_noise_only_no_accepted():
    rng = np.random.default_rng(0)
    t = np.arange(1600) / FS
    x = rng.normal(0.0, 0.05, t.size)
    acc = accepted_peaks(detect(x, t, AlgoParams()))
    assert len(acc) == 0


def test_dense_double_peak_rejected():
    t, x, times = _train(extra=[(0.6, 1.5)])
    acc = accepted_peaks(detect(x, t))
    assert len(acc) == len(times)  # 0.6 s 杂峰被峰距门剔除
    assert all(abs(a.t_s - 0.6) > 0.1 for a in acc)


def test_amplitude_outlier_rejected_end_to_end():
    t, x, times = _train(n=1200, amps=[1.4] * 5 + [3.0] + [1.4] * 5)
    cands = detect(x, t)
    acc = accepted_peaks(cands)
    assert len(acc) == 10  # 尖峰被剔除，链条不中断
    rejected = [c for c in cands if not c.accepted]
    assert any(c.reason == "邻搏幅度离群" for c in rejected)


def test_validity_interval_gate_unit():
    p = AlgoParams()
    t = np.arange(400) / FS
    raw = [(100, 1.0, 1.0), (118, 1.0, 1.0)]  # dt = 0.225 s
    out = _apply_validity(raw, t, p)
    assert out[0].accepted and out[1].reason == "峰距过近"


def test_validity_prominence_gate_unit():
    p = AlgoParams()
    t = np.arange(400) / FS
    raw = [(40, 1.0, 1.0), (120, 1.0, 0.95), (200, 1.0, 1.05), (280, 1.0, 0.10)]
    out = _apply_validity(raw, t, p)
    assert [o.accepted for o in out] == [True, True, True, False]
    assert out[3].reason == "prominence 不足"


def test_validity_lim_gate_unit():
    p = AlgoParams()
    t = np.arange(800) / FS
    proms = [1.0, 0.95, 1.05, 3.0, 1.0, 1.0, 0.95]
    raw = [(i * 80, 1.0, pr) for i, pr in enumerate(proms)]
    out = _apply_validity(raw, t, p)
    assert out[3].reason == "邻搏幅度离群"
    assert all(o.accepted for i, o in enumerate(out) if i != 3)


def test_validity_lim_gate_allows_ramp():
    """中心窗对单调爬升/回落豁免（趋势中值跟随候选）。"""
    p = AlgoParams()
    t = np.arange(1600) / FS
    proms = [0.4, 0.55, 0.75, 1.0, 1.3, 1.6, 1.9, 2.1, 1.9, 1.5, 1.1, 0.8, 0.55]
    raw = [(i * 90, 1.0, pr) for i, pr in enumerate(proms)]
    out = _apply_validity(raw, t, p)
    assert all(o.accepted for o in out)


def test_gradient_floor_and_adaptation():
    p = AlgoParams()
    g0 = _gradient_track(np.zeros(500), p)
    assert np.allclose(g0, p.grad_min)
    t, x, _ = _train(n=1600)
    g = _gradient_track(x, p)
    assert g.max() > 1.2 * p.grad_min  # 有信号块梯度应自适应抬升
    assert g.min() >= p.grad_min
