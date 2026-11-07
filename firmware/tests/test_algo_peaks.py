"""峰检测移植 vs PC 正本：候选索引/有效性完全一致，数值 <1e-9。"""

import numpy as np

from algo import peaks as fw_peaks
from algo.params import AlgoParams as FirmwareParams
from bp import peaks as pc_peaks
from bp.params import AlgoParams as PcParams

TOL = 1e-9
FS = 80.0


def _train(n=1600, rr=0.85, width=0.08, phase0=0.4, dicrotic=0.0, extra=None):
    t = np.arange(n) / FS
    x = np.zeros(n)
    times = np.arange(phase0, t[-1] - 0.1, rr)
    for bt in times:
        x += 2.0 * np.exp(-0.5 * ((t - bt) / width) ** 2)
        if dicrotic:
            x += dicrotic * np.exp(-0.5 * ((t - bt - 0.15) / width) ** 2)
    if extra:
        for bt, amp in extra:
            x += amp * np.exp(-0.5 * ((t - bt) / width) ** 2)
    return t, x


def _compare_candidates(fw_list, pc_list):
    assert len(fw_list) == len(pc_list)
    for fw, pc in zip(fw_list, pc_list):
        assert fw.idx == pc.idx
        assert fw.accepted == pc.accepted
        assert fw.reason == pc.reason
        assert abs(fw.t_s - pc.t_s) < TOL
        assert abs(fw.val - pc.val) < TOL
        assert abs(fw.prom - pc.prom) < TOL


def test_synthetic_pulse_train_matches_pc():
    fw_p = FirmwareParams()
    pc_p = PcParams()
    for dicrotic, extra in (
        (0.0, None),
        (0.5, None),
        (0.0, [(0.6, 1.5)]),
    ):
        t, x = _train(dicrotic=dicrotic, extra=extra)
        fw_list = fw_peaks.detect(x, t, fw_p)
        pc_list = pc_peaks.detect(x, t, pc_p)
        _compare_candidates(fw_list, pc_list)


def test_sim_deflate_candidates_match_pc(pc_normal_case):
    _rec, res = pc_normal_case
    fw_p = FirmwareParams()
    pc_p = PcParams()
    fw_list = fw_peaks.detect(res.ac, res.t_s, fw_p)
    pc_list = pc_peaks.detect(res.ac, res.t_s, pc_p)
    _compare_candidates(fw_list, pc_list)
    assert len(fw_list) == len(res.candidates)
    assert len(fw_peaks.accepted_peaks(fw_list)) == res.n_accepted
