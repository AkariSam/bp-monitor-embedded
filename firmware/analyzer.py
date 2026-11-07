"""固件会话式分析器：把纯 Python 算法管线接入 ControlFSM 的 ANALYZE。

协议：``start(buffer)`` 初始化，``step()`` 每次推进一个阶段，返回 None 表示
未完成；最后一步返回 ``{"result": "OK"/"REJECT", "reason": ..., "data": ...}``。

内存策略（S5 整改）：
- DC/AC 滤波结果存 ``array('f')``，滤波与延迟补偿均原地完成；
- 不生成裁剪副本，用 ``_SliceArray`` 视图跳过两端 margin；
- 不单独构造 ``t_s`` 数组，``_TimeView`` 按峰索引从 buffer 的 16-bit
  时间增量累计取时刻；
- DC 滤波拆成前后两半（状态续接），AC 拆成 HP / LP 两级，保证 9600 点
  最坏情况下单阶段仍小于 250 ms。
"""

from algo import decision as decision_mod
from algo import envelope as envelope_mod
from algo import filters as filters_mod
from algo import peaks as peaks_mod
from algo import qc as qc_mod
from algo.params import AlgoParams

# 阶段顺序；与 ControlFSM 的 start/step 会话协议兼容。
_STAGES = (
    "_stage_prepare",
    "_stage_dc_init",
    "_stage_dc_first",
    "_stage_dc_second",
    "_stage_ac_init",
    "_stage_ac_hp",
    "_stage_ac_lp",
    "_stage_peaks",
    "_stage_envelope",
    "_stage_decision",
    "_stage_qc",
)


class _ScaledArray(object):
    """array('i')/array('H') 的只读视图：按需缩放，避免复制输入数组。"""

    def __init__(self, values, scale=1.0, offset=0.0):
        self.values = values
        self.scale = scale
        self.offset = offset

    def __len__(self):
        return len(self.values)

    def __getitem__(self, index):
        return self.values[index] * self.scale + self.offset


class _SliceArray(object):
    """array('f') 的只读切片视图；避免为 margin 裁剪复制整段。"""

    def __init__(self, values, start, stop):
        self.values = values
        self.start = start
        self.stop = stop

    def __len__(self):
        return self.stop - self.start

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step == 1:
                return self.values[self.start + start:self.start + stop]
            return [
                self.values[self.start + i] for i in range(start, stop, step)
            ]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError("slice view index")
        return self.values[self.start + index]


class _TimeView(object):
    """按索引从 buffer 的 16-bit 增量累计绝对时间 [s]。

    ``peaks.detect`` 只按峰索引递增访问，内部游标使累计为 O(n)；
    若发生回退访问则从头重算。
    """

    def __init__(self, buffer, start, stop):
        self.buffer = buffer
        self.start = start
        self.stop = stop
        self._cursor = 0
        self._acc = 0

    def __len__(self):
        return self.stop - self.start

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError("time view index")
        target = self.start + index
        if target < self._cursor:
            self._cursor = 0
            self._acc = 0
        while self._cursor < target:
            self._cursor += 1
            self._acc += self.buffer.t_ms[self._cursor]
        origin = self.buffer.t_origin_ms
        if origin is None:
            origin = 0
        return (origin + self._acc) * 0.001


class PipelineAnalyzer(object):
    """分阶段执行 滤波 → 峰值 → 包络 → 判定 → QC。"""

    def __init__(self, params=None):
        self.params = params if params is not None else AlgoParams()
        self.start_calls = 0
        self.step_calls = 0
        self.result = None
        self._reset()

    def _reset(self):
        self.buffer = None
        self.n_samples = 0
        self.margin = 0
        self.stage = 0
        self.result = None
        self.p_view = None
        self.time_view = None
        self.t_s = None
        self.dc = None
        self.ac = None
        self.dc_view = None
        self.ac_view = None
        self.dc_sos = None
        self.dc_coeff = None
        self.dc_zi = None
        self.dc_state = None
        self.dc_pos = 0
        self.dc_delay = 0.0
        self.ac_sos = None
        self.ac_coeff_hp = None
        self.ac_coeff_lp = None
        self.ac_zi_hp = None
        self.ac_zi_lp = None
        self.ac_delay = 0.0
        self.candidates = None
        self.accepted = None
        self.envelope = None
        self.decision = None
        self.qc = None

    def start(self, buffer):
        self._reset()
        self.start_calls += 1
        self.buffer = buffer
        self.n_samples = len(buffer)
        self.margin = int(round(self.params.edge_margin_s * self.params.fs))

    def step(self):
        if self.result is not None:
            return self.result
        self.step_calls += 1
        getattr(self, _STAGES[self.stage])()
        return self.result

    # ------------------------------------------------------------------
    def _stage_prepare(self):
        if self.n_samples == 0:
            self._finish_reject("无 DEFLATE 段")
            return
        if self.n_samples <= 3 * self.margin:
            self._finish_reject("DEFLATE 段过短")
            return
        self.p_view = _ScaledArray(
            self.buffer.pressure_centimmHg, 0.01
        )
        self.time_view = _TimeView(
            self.buffer, self.margin, self.n_samples - self.margin
        )
        self.t_s = self.time_view
        self.stage = 1

    def _stage_dc_init(self):
        self.dc = filters_mod.f32_array(self.p_view)
        self.dc_sos = filters_mod.design_dc(self.params)
        self.dc_coeff = filters_mod.normalized_coeffs(self.dc_sos[0])
        self.dc_zi = filters_mod.sosfilt_zi(self.dc_sos)[0]
        self.dc_state = None
        self.dc_pos = 0
        self.dc_delay = filters_mod.group_delay_samples(
            self.dc_sos, self.params.fs, self.params.gd_dc_ref_hz
        )
        self.stage = 2

    def _stage_dc_first(self):
        half = (self.n_samples + 1) // 2
        self.dc_state = filters_mod.filter_section_inplace(
            self.dc, self.dc_coeff, self.dc_zi, None, 0, half
        )
        self.dc_pos = half
        self.stage = 3

    def _stage_dc_second(self):
        self.dc_state = filters_mod.filter_section_inplace(
            self.dc,
            self.dc_coeff,
            self.dc_zi,
            self.dc_state,
            self.dc_pos,
            self.n_samples,
        )
        self.dc_state = None
        filters_mod.compensate_inplace(self.dc, self.dc_delay)
        self.dc_view = _SliceArray(
            self.dc, self.margin, self.n_samples - self.margin
        )
        self.stage = 4

    def _stage_ac_init(self):
        self.ac = filters_mod.f32_array(self.p_view)
        self.ac_sos = filters_mod.design_ac(self.params)
        self.ac_coeff_hp = filters_mod.normalized_coeffs(self.ac_sos[0])
        self.ac_coeff_lp = filters_mod.normalized_coeffs(self.ac_sos[1])
        zi = filters_mod.sosfilt_zi(self.ac_sos)
        self.ac_zi_hp = zi[0]
        self.ac_zi_lp = zi[1]
        self.ac_delay = filters_mod.phase_delay_samples(
            self.ac_sos, self.params.fs, self.params.gd_ac_ref_hz
        )
        self.stage = 5

    def _stage_ac_hp(self):
        filters_mod.filter_section_inplace(
            self.ac,
            self.ac_coeff_hp,
            self.ac_zi_hp,
            None,
            0,
            self.n_samples,
        )
        self.stage = 6

    def _stage_ac_lp(self):
        filters_mod.filter_section_inplace(
            self.ac,
            self.ac_coeff_lp,
            self.ac_zi_lp,
            None,
            0,
            self.n_samples,
        )
        filters_mod.compensate_inplace(self.ac, self.ac_delay)
        self.ac_view = _SliceArray(
            self.ac, self.margin, self.n_samples - self.margin
        )
        self.stage = 7

    def _stage_peaks(self):
        self.candidates = peaks_mod.detect(
            self.ac,
            self.time_view,
            self.params,
            self.margin,
            self.n_samples - self.margin,
        )
        self.accepted = peaks_mod.accepted_peaks(self.candidates)
        self.stage = 8

    def _stage_envelope(self):
        pc, amp = envelope_mod.from_peaks(
            self.dc_view, self.ac_view, self.accepted
        )
        self.envelope = envelope_mod.build(pc, amp, self.params)
        self.stage = 9

    def _stage_decision(self):
        peak_times = []
        for candidate in self.accepted:
            peak_times.append(candidate.t_s)
        self.decision = decision_mod.decide(
            self.envelope, peak_times, self.params
        )
        self.stage = 10

    def _stage_qc(self):
        self.qc = qc_mod.check(
            self.envelope,
            self.candidates,
            self.decision,
            self.params,
        )
        self._finish_result()

    # ------------------------------------------------------------------
    def _finish_reject(self, reason):
        data = {
            "sbp": None,
            "dbp": None,
            "map": None,
            "hr": None,
            "dia_delta": None,
            "dia_flag": False,
            "ipp_ratio": None,
            "arrhythmia": False,
            "hr_flag": False,
            "n_candidates": 0,
            "n_accepted": 0,
            "n_points": 0,
            "n_samples": self.n_samples,
            "env_cv": None,
            "outlier_ratio": None,
            "qc_ok": False,
            "qc_reasons": [reason],
            "fs": self.params.fs,
        }
        self.result = {
            "result": "REJECT",
            "reason": reason,
            "data": data,
        }

    def _finish_result(self):
        decision = self.decision
        qc = self.qc
        accepted = (
            qc.ok
            and decision.sbp is not None
            and decision.dbp is not None
            and decision.map is not None
            and decision.hr is not None
        )
        if accepted:
            result = "OK"
            reason = "NONE"
        else:
            result = "REJECT"
            if qc.reasons:
                reason = " | ".join(qc.reasons)
            else:
                reason = "NO_RESULT"
        data = {
            "sbp": decision.sbp,
            "dbp": decision.dbp,
            "map": decision.map,
            "hr": decision.hr,
            "dia_delta": decision.dia_delta,
            "dia_flag": decision.dia_flag,
            "ipp_ratio": decision.ipp_ratio,
            "arrhythmia": decision.arrhythmia,
            "hr_flag": decision.hr_flag,
            "n_candidates": len(self.candidates),
            "n_accepted": len(self.accepted),
            "n_points": self.envelope.n_points,
            "n_samples": self.n_samples,
            "env_cv": qc.env_cv,
            "outlier_ratio": qc.outlier_ratio,
            "qc_ok": qc.ok,
            "qc_reasons": list(qc.reasons),
            "fs": self.params.fs,
        }
        self.result = {
            "result": result,
            "reason": reason,
            "data": data,
        }

