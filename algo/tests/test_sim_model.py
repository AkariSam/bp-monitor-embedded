"""sim/model.py 单元测试：DC 四段斜坡、准静态包络、脉搏波形、搏动序列。"""

from dataclasses import replace

import numpy as np

from sim.model import (FS_SIM, PhysioParams, beat_amplitude, beat_times,
                       dc_ramp, pulse_wave, synthesize, volume)


def test_dc_ramp_phase_sequence():
    _, _, phase = dc_ramp(PhysioParams())
    seq = []
    for ph in phase:
        if not seq or seq[-1] != ph:
            seq.append(ph)
    assert seq == ["INFLATE", "SETTLE", "DEFLATE", "DUMP"]


def test_dc_ramp_continuous_and_rates():
    params = PhysioParams()
    t, p, phase = dc_ramp(params)
    # 段边界无跳变：最大逐样本跳变不超过快排速率
    assert np.max(np.abs(np.diff(p))) <= params.dump_rate / FS_SIM + 1e-9

    m_def = phase == "DEFLATE"
    slope = np.polyfit(t[m_def], p[m_def], 1)[0]
    assert abs(slope + params.deflate_rate) < 1e-6  # 负斜率 = -deflate_rate

    m_set = phase == "SETTLE"
    assert abs(m_set.sum() / FS_SIM - params.settle_s) < 2 / FS_SIM


def test_volume_erf_monotonic_continuous():
    params = PhysioParams()
    sn, sp, mu = params.resolved_shape()
    pt = np.linspace(-400, 400, 1601)
    v = volume(pt, params.v_max, sn, sp, mu)
    assert np.all(np.diff(v) >= 0)                     # 单调不减
    mid = (pt > 0) & (pt < 100)                        # 避开两端 erf 饱和平台
    assert np.all(np.diff(v[mid]) > 0)                 # 中部严格递增
    assert v[0] < 0.01 * params.v_max                  # 负端 → 0
    assert v[-1] > 0.99 * params.v_max                 # 正端 → v_max
    # 分片点（峰位 μ）C¹ 光滑：两侧数值斜率均等于 C(μ) = C₀
    eps = 1e-6
    rt = np.sqrt(np.pi / 2.0)
    c0 = params.v_max / (rt * (sn + sp))
    v0 = float(volume(np.array([mu]), params.v_max, sn, sp, mu)[0])
    vl = float(volume(np.array([mu - eps]), params.v_max, sn, sp, mu)[0])
    vr = float(volume(np.array([mu + eps]), params.v_max, sn, sp, mu)[0])
    assert abs((v0 - vl) - c0 * eps) < 1e-9
    assert abs((vr - v0) - c0 * eps) < 1e-9


def _envelope_metrics(p: PhysioParams):
    """(r_s, r_d, k, Amax, Ppeak)，供锚点/自相似测试共用（峰值抛物线细化）。"""
    pc = np.linspace(p.dbp - 60.0, p.sbp + 60.0, 32001)
    a = beat_amplitude(pc, p)
    amax = float(np.max(a))
    rs = float(beat_amplitude(np.array([p.sbp]), p)[0]) / amax
    rd = float(beat_amplitude(np.array([p.dbp]), p)[0]) / amax
    i = int(np.argmax(a))
    ppeak = float(pc[i])
    if 0 < i < pc.size - 1:
        y0, y1, y2 = a[i - 1], a[i], a[i + 1]
        denom = y0 - 2.0 * y1 + y2
        if abs(denom) > 1e-30:
            delta = 0.5 * (y0 - y2) / denom
            ppeak = float(pc[i] + delta * (pc[1] - pc[0]))
    return rs, rd, (ppeak - p.dbp) / p.pp, amax, ppeak


def test_envelope_peak_at_consensus_anchor():
    params = PhysioParams()
    pc = np.linspace(50, 170, 24001)
    a = beat_amplitude(pc, params)
    peak_pc = pc[int(np.argmax(a))]
    assert abs(peak_pc - params.map_model) < 0.05
    # consensus 锚点：峰值 ≈ 0.41·S + 0.59·D（k=0.41）
    assert abs(peak_pc - (0.41 * params.sbp + 0.59 * params.dbp)) < 0.5


def test_edge_ratio_aligned_with_pipeline_constants():
    """默认形状的边缘比率精确等于管线常数 0.50 / 0.70（对齐守门测试）。"""
    params = PhysioParams()
    rs, rd, _, _, _ = _envelope_metrics(params)
    assert abs(rs - 0.50) < 0.005
    assert abs(rd - 0.70) < 0.005


def test_shape_self_similarity_across_bp_levels():
    """形状参数按脉压缩放：边缘比率与峰值分数跨血压水平不变。"""
    ref = PhysioParams()
    rs_ref, rd_ref, k_ref, _, _ = _envelope_metrics(ref)
    for sbp, dbp in [(160.0, 100.0), (90.0, 60.0), (150.0, 70.0)]:
        p = replace(ref, sbp=sbp, dbp=dbp)
        rs, rd, k, _, _ = _envelope_metrics(p)
        assert abs(rs - rs_ref) < 1e-4
        assert abs(rd - rd_ref) < 1e-4
        assert abs(k - k_ref) < 1e-3


def test_envelope_amplitude_in_physiological_range():
    params = PhysioParams()
    a_max = float(np.max(beat_amplitude(np.linspace(50, 170, 1201), params)))
    assert 1.0 <= a_max <= 3.0


def test_beat_count_matches_hr():
    params = PhysioParams()
    duration = 30.0
    beats = beat_times(params, duration, np.random.default_rng(0))
    expected = params.hr / 60.0 * duration
    assert abs(beats.size - expected) <= 2
    assert beats[0] == 0.0
    assert np.all(np.diff(beats) > 0)


def test_beat_jitter_changes_intervals():
    regular = beat_times(PhysioParams(), 30.0, np.random.default_rng(0))
    jitter = beat_times(replace(PhysioParams(), beat_jitter=0.2), 30.0,
                        np.random.default_rng(0))
    assert np.std(np.diff(regular)) < 1e-9
    assert np.std(np.diff(jitter)) > 0.01


def test_beat_times_deterministic_per_rng():
    params = replace(PhysioParams(), beat_jitter=0.2)
    a = beat_times(params, 30.0, np.random.default_rng(7))
    b = beat_times(params, 30.0, np.random.default_rng(7))
    assert np.array_equal(a, b)


def test_pulse_wave_shape():
    phi = np.linspace(0.0, 0.999, 2000)
    w = pulse_wave(phi)
    assert abs(w.max() - 1.0) < 0.05   # 归一化峰值 ≈ 1
    assert w.min() >= 0.0
    # 重搏波：φ≈0.35 处局部隆起（高于前后）
    assert pulse_wave(0.35) > pulse_wave(0.25)
    assert pulse_wave(0.35) > pulse_wave(0.45)


def test_synthesize_basic_properties():
    sig = synthesize(PhysioParams(), rng=np.random.default_rng(1))
    assert sig.t.size == sig.p_clean.size == sig.phase.size
    assert sig.t[0] == 0.0
    assert np.all(np.isfinite(sig.p_clean))

    # DEFLATE 段中部：去趋势后仍存在 1–3 mmHg 量级的振荡
    idx = np.where(sig.phase == "DEFLATE")[0]
    seg = slice(idx[len(idx) // 2] - 2500, idx[len(idx) // 2] + 2500)
    ts, ps = sig.t[seg], sig.p_clean[seg]
    detrended = ps - np.polyval(np.polyfit(ts, ps, 1), ts)
    assert 0.2 < detrended.std() < 2.0
