"""固件滤波移植 vs PC 正本：系数、延迟、因果滤波、sim DEFLATE 数据。

容差 1e-9 绝对值：PC 与固件均执行 float64，仅浮点运算顺序不同；
实测差异约 1e-11。
"""

import numpy as np
from scipy.signal import sosfilt_zi as pc_sosfilt_zi

from algo import filters as fw_filters
from algo.params import AlgoParams as FirmwareParams
from bp import filters as pc_filters
from bp.params import AlgoParams as PcParams

TOL = 1e-9


def test_design_coefficients_and_delays():
    fw_p = FirmwareParams()
    pc_p = PcParams()

    fw_dc = np.array(fw_filters.design_dc(fw_p))
    pc_dc = pc_filters.design_dc(pc_p)
    fw_ac = np.array(fw_filters.design_ac(fw_p))
    pc_ac = pc_filters.design_ac(pc_p)

    assert fw_dc.shape == pc_dc.shape
    assert fw_ac.shape == pc_ac.shape
    assert np.max(np.abs(fw_dc - pc_dc)) < TOL
    assert np.max(np.abs(fw_ac - pc_ac)) < TOL

    gd_dc_fw = fw_filters.group_delay_samples(fw_dc, fw_p.fs, fw_p.gd_dc_ref_hz)
    gd_dc_pc = pc_filters.group_delay_samples(pc_dc, pc_p.fs, pc_p.gd_dc_ref_hz)
    assert abs(gd_dc_fw - gd_dc_pc) < TOL

    gd_ac_fw = fw_filters.group_delay_samples(fw_ac, fw_p.fs, fw_p.gd_ac_ref_hz)
    gd_ac_pc = pc_filters.group_delay_samples(pc_ac, pc_p.fs, pc_p.gd_ac_ref_hz)
    assert abs(gd_ac_fw - gd_ac_pc) < TOL

    pd_fw = fw_filters.phase_delay_samples(fw_ac, fw_p.fs, fw_p.gd_ac_ref_hz)
    pd_pc = pc_filters.phase_delay_samples(pc_ac, pc_p.fs, pc_p.gd_ac_ref_hz)
    assert abs(pd_fw - pd_pc) < TOL


def test_sosfilt_zi_matches_pc():
    fw_p = FirmwareParams()
    pc_p = PcParams()
    fw_dc = np.array(fw_filters.sosfilt_zi(fw_filters.design_dc(fw_p)))
    pc_dc = pc_sosfilt_zi(pc_filters.design_dc(pc_p))
    fw_ac = np.array(fw_filters.sosfilt_zi(fw_filters.design_ac(fw_p)))
    pc_ac = pc_sosfilt_zi(pc_filters.design_ac(pc_p))
    assert np.max(np.abs(fw_dc - pc_dc)) < TOL
    assert np.max(np.abs(fw_ac - pc_ac)) < TOL


def test_causal_filter_and_compensation():
    fw_p = FirmwareParams()
    pc_p = PcParams()
    fw_dc = fw_filters.design_dc(fw_p)
    pc_dc = pc_filters.design_dc(pc_p)

    constant = [120.0] * 500
    fw_y = np.array(fw_filters.causal_filter(fw_dc, constant))
    pc_y = pc_filters.causal_filter(pc_dc, np.array(constant, dtype=float))
    assert np.max(np.abs(fw_y - pc_y)) < TOL

    ramp = [180.0 - 3.0 * i / 80.0 for i in range(1000)]
    fw_z = np.array(fw_filters.causal_filter(fw_dc, ramp))
    pc_z = pc_filters.causal_filter(pc_dc, np.array(ramp, dtype=float))
    assert np.max(np.abs(fw_z - pc_z)) < TOL

    for delay in (-7.4, -4.375, 0.0, 3.6, 37.4):
        fw_c = np.array(fw_filters.compensate(ramp[:300], delay))
        pc_c = pc_filters.compensate(np.array(ramp[:300], dtype=float), delay)
        assert np.max(np.abs(fw_c - pc_c)) < TOL


def test_filter_dc_ac_on_sim_deflate(pc_normal_case):
    rec, _res = pc_normal_case
    mask = rec.phase == "DEFLATE"
    p = rec.p_raw[mask]
    fw_p = FirmwareParams()
    pc_p = PcParams()

    fw_dc, fw_ac = fw_filters.filter_dc_ac(p.tolist(), fw_p)
    pc_dc, pc_ac = pc_filters.filter_dc_ac(p, pc_p)
    fw_dc = np.array(fw_dc)
    fw_ac = np.array(fw_ac)

    assert fw_dc.shape == pc_dc.shape
    assert fw_ac.shape == pc_ac.shape
    assert np.max(np.abs(fw_dc - pc_dc)) < TOL
    assert np.max(np.abs(fw_ac - pc_ac)) < TOL


def test_inplace_array_filter_chunking_and_compensation():
    fw_p = FirmwareParams()
    ramp = [180.0 - 3.0 * i / 80.0 for i in range(1000)]
    sos = fw_filters.design_dc(fw_p)
    coeffs = fw_filters.normalized_coeffs(sos[0])
    zi = fw_filters.sosfilt_zi(sos)[0]

    single = fw_filters.f32_array(ramp)
    fw_filters.filter_section_inplace(
        single, coeffs, zi, None, 0, len(single)
    )
    reference = np.array(fw_filters.causal_filter(sos, ramp))
    assert np.max(np.abs(np.array(single) - reference)) < 1e-4

    chunked = fw_filters.f32_array(ramp)
    state = fw_filters.filter_section_inplace(
        chunked, coeffs, zi, None, 0, 500
    )
    fw_filters.filter_section_inplace(
        chunked, coeffs, zi, state, 500, len(chunked)
    )
    assert np.max(np.abs(np.array(chunked) - np.array(single))) < 1e-6

    shifted = fw_filters.f32_array(ramp[:300])
    fw_filters.compensate_inplace(shifted, 37.4)
    expected = np.array(fw_filters.compensate(ramp[:300], 37.4))
    assert np.max(np.abs(np.array(shifted) - expected)) < 1e-4
