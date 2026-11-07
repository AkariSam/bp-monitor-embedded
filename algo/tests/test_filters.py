"""滤波器设计与群延迟的单元测试。"""

import numpy as np
from scipy.signal import find_peaks

from bp.filters import (
    causal_filter,
    compensate,
    design_ac,
    design_dc,
    filter_dc_ac,
    group_delay_samples,
    phase_delay_samples,
)
from bp.params import AlgoParams


def _gain(sos, f, fs=80.0, n=8000):
    """正弦稳态增益（取后半段峰值比，规避启动段）。"""
    t = np.arange(n) / fs
    x = np.sin(2 * np.pi * f * t)
    y = causal_filter(sos, x)
    m = n // 2
    return float(np.max(np.abs(y[m:])) / np.max(np.abs(x[m:])))


def test_design_sections():
    p = AlgoParams()
    assert design_dc(p).shape == (1, 6)
    assert design_ac(p).shape == (2, 6)  # HP 2 阶 + LP 2 阶 = 2 段 biquad


def test_dc_lowpass_response():
    sos = design_dc(AlgoParams())
    assert _gain(sos, 0.1) > 0.95
    assert _gain(sos, 8.0) < 0.05


def test_ac_bandpass_response():
    sos = design_ac(AlgoParams())
    assert _gain(sos, 1.2) > 0.9      # 脉搏主频
    assert _gain(sos, 0.1) < 0.10     # 呼吸/漂移阻断
    assert _gain(sos, 30.0) < 0.15    # 高频噪声阻断


def test_group_delay_positive_and_reasonable():
    p = AlgoParams()
    gd_ac = group_delay_samples(design_ac(p), p.fs, p.gd_ac_ref_hz)
    gd_dc = group_delay_samples(design_dc(p), p.fs, p.gd_dc_ref_hz)
    assert 0.0 < gd_ac < 40.0
    assert 0.0 < gd_dc < 40.0
    pd_ac = phase_delay_samples(design_ac(p), p.fs, p.gd_ac_ref_hz)
    assert abs(pd_ac) < 40.0


def test_startup_transient_suppressed():
    """稳态初始化后，恒定输入不应产生启动振荡。"""
    sos = design_ac(AlgoParams())
    x = np.full(2000, 100.0)
    y = causal_filter(sos, x)
    assert np.max(np.abs(y)) < 1e-6


def test_ac_compensation_aligns_sine():
    """已知延迟正弦：按相位延迟补偿后峰位置与原始信号对齐（≤1 采样）。"""
    fs, n, f = 80.0, 1600, 1.2
    t = np.arange(n) / fs
    x = 100.0 + 20.0 * np.sin(2 * np.pi * f * t)
    true_idx, _ = find_peaks(x)
    ac = causal_filter(design_ac(AlgoParams()), x)

    pk, _ = find_peaks(ac, height=5.0)
    pk = pk[(pk > 200) & (pk < n - 200)]
    lags = [int(pk[i] - true_idx[np.argmin(np.abs(true_idx - pk[i]))])
            for i in range(pk.size)]
    lag = int(np.median(lags))

    pd = phase_delay_samples(design_ac(AlgoParams()), fs, f)
    assert abs(lag - round(pd)) <= 2

    ac_c = compensate(ac, pd)
    pkc, _ = find_peaks(ac_c, height=5.0)
    pkc = pkc[(pkc > 200) & (pkc < n - 200)]
    lags2 = [int(pkc[i] - true_idx[np.argmin(np.abs(true_idx - pkc[i]))])
             for i in range(pkc.size)]
    assert abs(np.median(lags2)) <= 1


def test_dc_compensation_aligns_ramp():
    """已知斜率斜坡：按群延迟补偿后时基对齐。"""
    p = AlgoParams()
    fs, n = p.fs, 8000
    t = np.arange(n) / fs
    x = 180.0 - 3.0 * t
    dc = causal_filter(design_dc(p), x)
    gd = group_delay_samples(design_dc(p), fs, p.gd_dc_ref_hz)
    dc_c = compensate(dc, gd)
    k = n - 500
    assert abs(dc_c[k] - (180.0 - 3.0 * t[k])) < 0.2


def test_filter_dc_ac_shapes_and_dc_tracking():
    """DC 应跟随缓慢下降的袖带压，AC 应围绕零振荡。"""
    fs, n = 80.0, 4000
    t = np.arange(n) / fs
    x = 180.0 - 3.0 * t + 2.0 * np.sin(2 * np.pi * 1.2 * t)
    dc, ac = filter_dc_ac(x)
    assert dc.shape == x.shape and ac.shape == x.shape
    k = n - 200
    assert abs(dc[k] - (180.0 - 3.0 * t[k])) < 0.5
    assert abs(np.median(ac[n // 2:])) < 0.5
