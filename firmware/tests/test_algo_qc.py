"""质控移植 vs PC 正本：ok/reasons/CV/离群占比，含 sim 与构造用例。"""

import numpy as np

from algo import envelope as fw_env
from algo import qc as fw_qc
from algo.params import AlgoParams as FirmwareParams
from bp import decision as pc_decision
from bp import envelope as pc_env
from bp import peaks as pc_peaks
from bp import qc as pc_qc
from bp.params import AlgoParams as PcParams

TOL = 1e-9


def _compare(fw, pc):
    assert fw.ok == pc.ok
    assert fw.reasons == pc.reasons
    for name in ("outlier_ratio", "env_cv"):
        a = getattr(fw, name)
        b = getattr(pc, name)
        if a is None or b is None:
            assert a is None and b is None, name
        else:
            assert abs(a - b) < TOL, name


def test_sim_normal_qc_matches_pc(pc_normal_case):
    _rec, res = pc_normal_case
    fw_p = FirmwareParams()
    pc_p = PcParams()
    _compare(
        fw_qc.check(res.envelope, res.candidates, res.decision, fw_p),
        pc_qc.check(res.envelope, res.candidates, res.decision, pc_p),
    )


def test_sim_truncated_qc_matches_pc(pc_case_loader):
    _rec, res = pc_case_loader("truncated_s001")
    fw_p = FirmwareParams()
    pc_p = PcParams()
    _compare(
        fw_qc.check(res.envelope, res.candidates, res.decision, fw_p),
        pc_qc.check(res.envelope, res.candidates, res.decision, pc_p),
    )


def test_short_envelope_reason_matches_pc():
    fw_p = FirmwareParams()
    pc_p = PcParams()
    pc = np.arange(5.0) * 4.0 + 60.0
    amp = np.ones(5)
    env = pc_env.build(pc, amp, pc_p)
    decision = pc_decision.Decision()
    _compare(
        fw_qc.check(env, [], decision, fw_p),
        pc_qc.check(env, [], decision, pc_p),
    )


def test_rough_envelope_reason_matches_pc():
    fw_p = FirmwareParams()
    pc_p = PcParams()
    pc = np.arange(10.0) * 4.0 + 60.0
    amp = 1.0 + 0.5 * ((-1.0) ** np.arange(10))
    env = pc_env.build(pc, amp, pc_p)
    decision = pc_decision.Decision()
    _compare(
        fw_qc.check(env, [], decision, fw_p),
        pc_qc.check(env, [], decision, pc_p),
    )


def test_outlier_ratio_reason_matches_pc():
    fw_p = FirmwareParams()
    pc_p = PcParams()
    pc = np.arange(10.0) * 4.0 + 60.0
    env = pc_env.build(pc, np.ones(10), pc_p)
    cands = [
        pc_peaks.Candidate(i, float(i), 1.0, 1.0, True, "")
        for i in range(6)
    ]
    cands += [
        pc_peaks.Candidate(100 + i, 100.0 + i, 1.0, 3.0, False, "邻搏幅度离群")
        for i in range(4)
    ]
    decision = pc_decision.Decision()
    _compare(
        fw_qc.check(env, cands, decision, fw_p),
        pc_qc.check(env, cands, decision, pc_p),
    )
