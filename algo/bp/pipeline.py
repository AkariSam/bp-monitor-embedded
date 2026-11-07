"""端到端编排：CSV/Recording → 结果 + 过程量（§4.0 工作流）。

流程：DEFLATE 切片 → DC/AC 因果滤波与时基对齐 → 峰检测与有效性 →
逐搏包络 → 平滑 → 判定（比率法）→ 质控。过程量全部暴露供出图与
PC↔C 对拍；**本模块（及 bp 包全部）不读真值**，真值只在脚本/测试侧
用于评估。
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .csvio import Recording, read_csv
from .decision import Decision, decide
from .envelope import Envelope, build, from_peaks
from .filters import filter_dc_ac
from .params import AlgoParams
from .peaks import Candidate, accepted_peaks, detect
from .qc import QCReport, check


@dataclass(frozen=True)
class PipelineResult:
    """管线输出：判定 + 质控 + 过程量。"""

    accepted: bool
    decision: Decision
    qc: QCReport
    fs: float
    n_candidates: int
    n_accepted: int
    t_s: np.ndarray
    dc: np.ndarray
    ac: np.ndarray
    candidates: tuple[Candidate, ...]
    envelope: Envelope


def _empty_result(params: AlgoParams, reason: str) -> PipelineResult:
    env = build([], [], params)
    dec = Decision(reasons=(reason,))
    qc = check(env, [], dec, params)
    return PipelineResult(
        accepted=False, decision=dec, qc=qc, fs=params.fs,
        n_candidates=0, n_accepted=0,
        t_s=np.array([]), dc=np.array([]), ac=np.array([]),
        candidates=(), envelope=env,
    )


def run(rec: Recording, params: AlgoParams | None = None) -> PipelineResult:
    """对一次记录执行完整管线。"""
    params = params or AlgoParams()
    mask = rec.phase == "DEFLATE"
    if not mask.any():
        return _empty_result(params, "无 DEFLATE 段")

    t = rec.t_ms[mask] / 1000.0
    p = rec.p_raw[mask]
    dc, ac = filter_dc_ac(p, params)

    margin = int(round(params.edge_margin_s * params.fs))
    if t.size <= 3 * margin:
        return _empty_result(params, "DEFLATE 段过短")
    t, dc, ac = t[margin:-margin], dc[margin:-margin], ac[margin:-margin]

    candidates = detect(ac, t, params)
    acc = accepted_peaks(candidates)

    pc, amp = from_peaks(dc, ac, acc)
    env = build(pc, amp, params)
    decision = decide(env, [c.t_s for c in acc], params)
    qc = check(env, candidates, decision, params)

    accepted = (qc.ok and decision.sbp is not None
                and decision.dbp is not None and decision.map is not None)
    return PipelineResult(
        accepted=accepted, decision=decision, qc=qc, fs=params.fs,
        n_candidates=len(candidates), n_accepted=len(acc),
        t_s=t, dc=dc, ac=ac,
        candidates=tuple(candidates), envelope=env,
    )


def run_csv(path: str | Path, params: AlgoParams | None = None) -> PipelineResult:
    """读取契约 CSV 并执行管线。"""
    return run(read_csv(path), params)
