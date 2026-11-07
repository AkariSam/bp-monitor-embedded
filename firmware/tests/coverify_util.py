"""S3/S4 对拍工具：8 场景加载、固件缓冲、PC 同缓冲参考。

S3 的严格口径是 ``pc_on_buffer``：PC 正本直接吃固件 ``DeflateBuffer`` 的
整数毫秒时间与 0.01 mmHg 压力，避免 CSV 原始 12.5 ms 网格与设备毫秒时基
之间的取整差异混入对拍。``pc_on_recording`` 保留 CSV 原始口径，仅作参考。
"""

import json
from pathlib import Path

import numpy as np

from analyzer import PipelineAnalyzer
from buffer import DeflateBuffer

from bp import decision as pc_decision
from bp import envelope as pc_envelope
from bp import filters as pc_filters
from bp import peaks as pc_peaks
from bp import qc as pc_qc
from bp.csvio import read_csv
from bp.params import AlgoParams as PcParams
from bp.pipeline import run as pc_pipeline_run

SCENARIOS = (
    "normal",
    "hypertension",
    "hypotension",
    "wide_pp",
    "af",
    "bradycardia",
    "tachycardia",
    "truncated",
)

ALGO_DIR = Path(__file__).resolve().parents[2] / "algo"
DATA_DIR = ALGO_DIR / "data" / "sim"
FALLBACK_DIR = Path(__file__).resolve().parents[2] / "tmp" / "coverify_sim"


class PcRun(object):
    """PC 同缓冲运行的输出（字段与 PipelineResult 对齐）。"""

    def __init__(
        self,
        accepted,
        decision,
        qc,
        candidates,
        accepted_peaks,
        envelope,
        t_s,
        dc,
        ac,
    ):
        self.accepted = bool(accepted)
        self.decision = decision
        self.qc = qc
        self.candidates = candidates
        self.accepted_peaks = accepted_peaks
        self.envelope = envelope
        self.t_s = t_s
        self.dc = dc
        self.ac = ac


def load_scene(name, seed=1):
    """返回 ``(rec, truth, csv_path, truth_path)``；缺失时按 seed 重生成。"""
    stem = "%s_s%03d" % (name, seed)
    csv_path = DATA_DIR / (stem + ".csv")
    truth_path = DATA_DIR / (stem + ".truth.json")
    if not csv_path.exists() or not truth_path.exists():
        from sim.generate import generate

        csv_path, truth_path = generate(name, seed, FALLBACK_DIR)
    rec = read_csv(str(csv_path))
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    return rec, truth, csv_path, truth_path


def make_buffer(rec, max_samples=None):
    """按固件契约把 Recording 的 DEFLATE 段写入 DeflateBuffer。"""
    mask = rec.phase == "DEFLATE"
    t_values = rec.t_ms[mask]
    p_values = rec.p_raw[mask]
    count = len(t_values)
    if max_samples is not None and count > max_samples:
        count = max_samples
    buffer = DeflateBuffer(max(1, count))
    for i in range(count):
        if not buffer.append(
            i,
            int(round(float(t_values[i]))),
            float(p_values[i]),
        ):
            raise AssertionError("DeflateBuffer append failed")
    return buffer


def run_firmware(buffer, params=None):
    """跑固件会话式分析器，返回 ``(result, analyzer, steps)``。"""
    analyzer = PipelineAnalyzer(params)
    analyzer.start(buffer)
    result = None
    steps = 0
    while result is None:
        result = analyzer.step()
        steps += 1
        if steps > 32:
            raise AssertionError("analyzer did not finish")
    return result, analyzer, steps


def pc_on_recording(rec, params=None):
    """PC 正本直接跑 CSV 原始口径（12.5 ms 浮点网格）。"""
    return pc_pipeline_run(rec, params)


def pc_on_buffer(buffer, params=None):
    """PC 正本跑固件缓冲的同输入口径。

    时间与压力逐样本复刻 ``analyzer._ScaledArray``：
    ``t = t_ms * 0.001 + origin * 0.001``、``p = centimmHg * 0.01``。
    其余步骤与 ``bp.pipeline.run`` 相同。
    """
    params = params or PcParams()
    n = len(buffer)
    origin = buffer.t_origin_ms
    if origin is None:
        origin = 0

    t_all = []
    t_acc = 0
    for i in range(n):
        if i:
            t_acc += buffer.t_ms[i]
        t_all.append((origin + t_acc) * 0.001)
    t_all = np.array(t_all, dtype=float)
    p = np.array(
        [buffer.pressure_centimmHg[i] * 0.01 for i in range(n)],
        dtype=float,
    )
    dc, ac = pc_filters.filter_dc_ac(p, params)

    margin = int(round(params.edge_margin_s * params.fs))
    if t_all.size <= 3 * margin:
        env = pc_envelope.build([], [], params)
        decision = pc_decision.Decision(reasons=("DEFLATE 段过短",))
        qc = pc_qc.check(env, [], decision, params)
        return PcRun(
            accepted=False,
            decision=decision,
            qc=qc,
            candidates=[],
            accepted_peaks=[],
            envelope=env,
            t_s=t_all,
            dc=dc,
            ac=ac,
        )

    t = t_all[margin:-margin]
    dc = dc[margin:-margin]
    ac = ac[margin:-margin]

    candidates = pc_peaks.detect(ac, t, params)
    accepted_peaks = pc_peaks.accepted_peaks(candidates)
    pc_points, amp = pc_envelope.from_peaks(dc, ac, accepted_peaks)
    env = pc_envelope.build(pc_points, amp, params)
    decision = pc_decision.decide(env, [c.t_s for c in accepted_peaks], params)
    qc = pc_qc.check(env, candidates, decision, params)
    accepted = (
        qc.ok
        and decision.sbp is not None
        and decision.dbp is not None
        and decision.map is not None
    )
    return PcRun(
        accepted=accepted,
        decision=decision,
        qc=qc,
        candidates=candidates,
        accepted_peaks=accepted_peaks,
        envelope=env,
        t_s=t,
        dc=dc,
        ac=ac,
    )


def pc_signal(run):
    """从 PC 运行结果提取对拍字段。"""
    decision = run.decision
    n_candidates = getattr(run, "n_candidates", None)
    if n_candidates is None:
        n_candidates = len(run.candidates)
    n_accepted = getattr(run, "n_accepted", None)
    if n_accepted is None:
        n_accepted = len(run.accepted_peaks)
    return {
        "accepted": bool(run.accepted),
        "sbp": decision.sbp,
        "dbp": decision.dbp,
        "map": decision.map,
        "hr": decision.hr,
        "n_candidates": n_candidates,
        "n_accepted": n_accepted,
        "n_points": run.envelope.n_points,
        "qc_ok": bool(run.qc.ok),
        "qc_reasons": list(run.qc.reasons),
        "env_cv": run.qc.env_cv,
        "outlier_ratio": run.qc.outlier_ratio,
    }


def firmware_signal(result):
    """从固件 result 提取对拍字段。"""
    data = result["data"]
    return {
        "accepted": result["result"] == "OK",
        "sbp": data["sbp"],
        "dbp": data["dbp"],
        "map": data["map"],
        "hr": data["hr"],
        "n_candidates": data["n_candidates"],
        "n_accepted": data["n_accepted"],
        "n_points": data["n_points"],
        "qc_ok": data["qc_ok"],
        "qc_reasons": list(data["qc_reasons"]),
        "env_cv": data["env_cv"],
        "outlier_ratio": data["outlier_ratio"],
    }
