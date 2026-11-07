"""S4：float32 敏感性验证。

把固件算法模块的中间量逐项往返到 binary32，对比 float64 固件与 PC 正本，
并单独量化四类风险：AC 极点漂移、DC 稳态初始化相消、延迟取整边界、
边缘搏动接受/剔除变化；另测 0.01 mmHg + 1 ms 输入量化耦合。
"""

import cmath

import pytest

from algo import filters
from algo.params import AlgoParams
from coverify_util import (
    SCENARIOS,
    firmware_signal,
    load_scene,
    make_buffer,
    pc_on_buffer,
    pc_on_recording,
    pc_signal,
    run_firmware,
)
from f32 import Float32Hooks


def _run_f32(buffer):
    with Float32Hooks():
        return run_firmware(buffer)


def _poles(section):
    a1 = section[4]
    a2 = section[5]
    disc = a1 * a1 - 4.0 * a2
    if disc >= 0.0:
        root = disc ** 0.5
        return complex((-a1 + root) / 2.0, 0.0), complex((-a1 - root) / 2.0, 0.0)
    real = -a1 / 2.0
    imag = (-disc) ** 0.5 / 2.0
    return complex(real, imag), complex(real, -imag)


@pytest.mark.parametrize("name", SCENARIOS)
def test_float32_pipeline_stable(name):
    rec, _truth, _csv_path, _truth_path = load_scene(name)
    buffer = make_buffer(rec)

    result64, analyzer64, _steps = run_firmware(buffer)
    result32, analyzer32, _steps = _run_f32(buffer)

    sig64 = firmware_signal(result64)
    sig32 = firmware_signal(result32)
    pc = pc_signal(pc_on_buffer(buffer))

    assert sig32["accepted"] == sig64["accepted"]
    assert sig32["qc_ok"] == sig64["qc_ok"]
    assert sig32["qc_reasons"] == sig64["qc_reasons"]
    assert sig32["n_candidates"] == sig64["n_candidates"]
    assert sig32["n_accepted"] == sig64["n_accepted"]
    assert sig32["n_points"] == sig64["n_points"]

    idx64 = [candidate.idx for candidate in analyzer64.accepted]
    idx32 = [candidate.idx for candidate in analyzer32.accepted]
    assert idx32 == idx64

    cand64 = [
        (candidate.idx, candidate.accepted, candidate.reason)
        for candidate in analyzer64.candidates
    ]
    cand32 = [
        (candidate.idx, candidate.accepted, candidate.reason)
        for candidate in analyzer32.candidates
    ]
    assert cand32 == cand64

    if sig64["accepted"]:
        assert abs(sig32["sbp"] - sig64["sbp"]) <= 0.1
        assert abs(sig32["dbp"] - sig64["dbp"]) <= 0.1
        assert abs(sig32["hr"] - sig64["hr"]) <= 0.1
        assert abs(sig32["sbp"] - pc["sbp"]) <= 1.0
        assert abs(sig32["dbp"] - pc["dbp"]) <= 1.0
        assert abs(sig32["hr"] - pc["hr"]) <= 1.0
    else:
        assert sig32["sbp"] is None


def test_ac_highpass_pole_drift():
    params = AlgoParams()
    sos64 = filters.design_ac(params)
    with Float32Hooks():
        sos32 = filters.design_ac(params)

    pole64, _ = _poles(sos64[0])
    pole32, _ = _poles(sos32[0])
    radius64 = abs(pole64)
    radius32 = abs(pole32)
    angle64 = cmath.phase(pole64)
    angle32 = cmath.phase(pole32)

    assert abs(radius32 - radius64) / radius64 < 1e-6
    assert abs(angle32 - angle64) < 1e-5

    low64, _ = _poles(sos64[1])
    low32, _ = _poles(sos32[1])
    assert abs(abs(low32) - abs(low64)) / abs(low64) < 1e-6
    assert abs(cmath.phase(low32) - cmath.phase(low64)) < 1e-5


def test_dc_steady_state_init_cancellation():
    params = AlgoParams()
    sos64 = filters.design_dc(params)
    zi64 = filters.sosfilt_zi(sos64)
    with Float32Hooks():
        sos32 = filters.design_dc(params)
        zi32 = filters.sosfilt_zi(sos32)

    for s in range(len(zi64)):
        for k in range(2):
            value64 = zi64[s][k]
            value32 = zi32[s][k]
            assert abs(value64 - value32) / abs(value64) < 1e-4

    assert 0.99 < zi64[0][0] < 1.01
    assert 0.99 < zi32[0][0] < 1.01


def test_delay_rounding_not_on_boundary():
    params = AlgoParams()
    sos_dc64 = filters.design_dc(params)
    sos_ac64 = filters.design_ac(params)
    gd64 = filters.group_delay_samples(sos_dc64, params.fs, params.gd_dc_ref_hz)
    pd64 = filters.phase_delay_samples(sos_ac64, params.fs, params.gd_ac_ref_hz)

    with Float32Hooks():
        sos_dc32 = filters.design_dc(params)
        sos_ac32 = filters.design_ac(params)
        gd32 = filters.group_delay_samples(
            sos_dc32, params.fs, params.gd_dc_ref_hz
        )
        pd32 = filters.phase_delay_samples(
            sos_ac32, params.fs, params.gd_ac_ref_hz
        )

    assert int(round(gd64)) == int(round(gd32)) == 37
    assert int(round(pd64)) == int(round(pd32)) == -4
    assert abs(gd64 - 37.5) > 0.05
    assert abs(gd32 - 37.5) > 0.05
    assert abs(gd32 - gd64) < 0.1
    assert abs(pd32 - pd64) < 0.01


@pytest.mark.parametrize("name", SCENARIOS)
def test_input_quantization_coupling(name):
    """0.01 mmHg 压力 + 1 ms 时间量化对 PC 结果的影响。"""
    rec, _truth, _csv_path, _truth_path = load_scene(name)
    buffer = make_buffer(rec)
    original = pc_signal(pc_on_recording(rec))
    quantized = pc_signal(pc_on_buffer(buffer))

    assert quantized["accepted"] == original["accepted"]
    assert quantized["qc_reasons"] == original["qc_reasons"]

    if original["accepted"]:
        assert abs(quantized["sbp"] - original["sbp"]) <= 1.0
        assert abs(quantized["dbp"] - original["dbp"]) <= 1.0
        assert abs(quantized["hr"] - original["hr"]) <= 1.0
        assert abs(quantized["sbp"] - original["sbp"]) < 1e-9
        assert abs(quantized["dbp"] - original["dbp"]) < 1e-9
    else:
        assert quantized["sbp"] is None
