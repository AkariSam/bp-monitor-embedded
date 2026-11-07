"""包络移植 vs PC 正本：平滑、排序、逐搏窗、sim 数据。"""

import numpy as np

from algo import envelope as fw_env
from algo.params import AlgoParams as FirmwareParams
from bp import envelope as pc_env
from bp import peaks as pc_peaks
from bp.params import AlgoParams as PcParams

TOL = 1e-9


def test_smooth_matches_pc_scipy():
    fw_p = FirmwareParams()
    pc_p = PcParams()
    cases = [
        np.ones(11),
        np.array([1.0, 2.0, 10.0, 2.0, 1.0, 1.0, 1.0]),
        np.linspace(0.5, 2.0, 20),
        np.array([]),
    ]
    cases[1][2] = 10.0
    for amp in cases:
        fw_y = np.array(fw_env.smooth(amp, fw_p.env_median, fw_p.env_ma))
        pc_y = pc_env.smooth(amp, pc_p.env_median, pc_p.env_ma)
        assert fw_y.shape == pc_y.shape
        if fw_y.size:
            assert np.max(np.abs(fw_y - pc_y)) < TOL


def test_from_peaks_and_build_on_sim(pc_normal_case):
    _rec, res = pc_normal_case
    fw_p = FirmwareParams()
    pc_p = PcParams()
    accepted_pc = pc_peaks.accepted_peaks(res.candidates)

    fw_pc, fw_amp = fw_env.from_peaks(res.dc, res.ac, accepted_pc)
    pc_pc, pc_amp = pc_env.from_peaks(res.dc, res.ac, accepted_pc)
    fw_pc = np.array(fw_pc)
    fw_amp = np.array(fw_amp)
    assert fw_pc.shape == pc_pc.shape
    assert fw_amp.shape == pc_amp.shape
    assert np.max(np.abs(fw_pc - pc_pc)) < TOL
    assert np.max(np.abs(fw_amp - pc_amp)) < TOL

    fw_built = fw_env.build(fw_pc, fw_amp, fw_p)
    pc_built = pc_env.build(pc_pc, pc_amp, pc_p)
    assert fw_built.n_points == pc_built.n_points
    assert np.max(np.abs(np.array(fw_built.pc) - pc_built.pc)) < TOL
    assert np.max(np.abs(np.array(fw_built.amp) - pc_built.amp)) < TOL
    assert np.max(np.abs(np.array(fw_built.amp_s) - pc_built.amp_s)) < TOL
