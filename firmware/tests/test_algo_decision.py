"""判定移植 vs PC 正本：MAP/SBP/DBP/HR/DIA/心律标志。"""

import numpy as np

from algo import decision as fw_decision
from algo import envelope as fw_env
from algo.params import AlgoParams as FirmwareParams
from bp import decision as pc_decision
from bp import envelope as pc_env
from bp import peaks as pc_peaks
from bp.params import AlgoParams as PcParams

TOL = 1e-9


def _compare(fw, pc):
    for name in ("map", "sbp", "dbp", "hr", "dia_delta", "ipp_ratio"):
        a = getattr(fw, name)
        b = getattr(pc, name)
        if a is None or b is None:
            assert a is None and b is None, name
        else:
            assert abs(a - b) < TOL, name
    assert fw.dia_flag == pc.dia_flag
    assert fw.arrhythmia == pc.arrhythmia
    assert fw.hr_flag == pc.hr_flag
    assert fw.reasons == pc.reasons


def test_analytic_bell_matches_pc():
    fw_p = FirmwareParams()
    pc_p = PcParams()
    pc = np.arange(60.0, 150.0 + 1.0, 2.0)
    amp = np.exp(-((pc - 100.0) / 15.0) ** 2)

    fw_env_obj = fw_env.build(pc, amp, fw_p)
    pc_env_obj = pc_env.build(pc, amp, pc_p)
    fw = fw_decision.decide(fw_env_obj, [], fw_p)
    p = pc_decision.decide(pc_env_obj, [], pc_p)
    _compare(fw, p)


def test_hr_and_arrhythmia_flags_match_pc():
    fw_p = FirmwareParams()
    pc_p = PcParams()
    pc = np.arange(60.0, 151.0, 2.0)
    amp = np.exp(-((pc - 100.0) / 15.0) ** 2)
    fw_env_obj = fw_env.build(pc, amp, fw_p)
    pc_env_obj = pc_env.build(pc, amp, pc_p)

    rr = 60.0 / 70.0
    times = [rr * i for i in range(21)]
    _compare(
        fw_decision.decide(fw_env_obj, times, fw_p),
        pc_decision.decide(pc_env_obj, times, pc_p),
    )

    jitter = [0.0]
    for i in range(20):
        jitter.append(jitter[-1] + rr * (1.3 if i % 2 else 0.85))
    _compare(
        fw_decision.decide(fw_env_obj, jitter, fw_p),
        pc_decision.decide(pc_env_obj, jitter, pc_p),
    )


def test_truncated_envelope_reasons_match_pc():
    fw_p = FirmwareParams()
    pc_p = PcParams()
    pc = np.arange(60.0, 110.0 + 1.0, 2.0)
    amp = np.exp(-((pc - 100.0) / 15.0) ** 2)
    _compare(
        fw_decision.decide(fw_env.build(pc, amp, fw_p), [], fw_p),
        pc_decision.decide(pc_env.build(pc, amp, pc_p), [], pc_p),
    )


def test_sim_decision_matches_pc(pc_normal_case):
    _rec, res = pc_normal_case
    fw_p = FirmwareParams()
    pc_p = PcParams()
    accepted = pc_peaks.accepted_peaks(res.candidates)
    times = [c.t_s for c in accepted]
    _compare(
        fw_decision.decide(res.envelope, times, fw_p),
        pc_decision.decide(res.envelope, times, pc_p),
    )
