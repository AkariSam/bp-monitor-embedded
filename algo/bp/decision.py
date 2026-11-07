"""判定：MAP / SBP / DBP / HR 与 DIA 交叉校验（§4.4）。

- MAP = 包络最大振幅处压力（3 点抛物线顶点细化，代替"线性插值加密"）。
- SBP = MAP 高压侧包络首次跌至 0.50×峰值（线性插值，比率 Flash 可调）。
- DBP = MAP 低压侧首次跌至 0.70×峰值；交叉从 MAP 向外找**首次**交点，
  天然取最靠近 MAP 的稳健交点（多交点时避免远尾假交叉）。
- DIA 交叉校验：DIA_formula = (3×MAP − SBP)/2；偏差 >10 mmHg 打质量标志
  （公式法系统性偏高，不作结果值）。
- HR = 60 / 有效峰间期中位数；IPP = 峰间期相对中位偏差 >0.2 的不规则占比，
  占比 >0.2 附「疑似心律不齐」标志（仅提示）。
"""

from dataclasses import dataclass

import numpy as np

from .envelope import Envelope
from .params import AlgoParams


@dataclass(frozen=True)
class Decision:
    """判定结果（缺交叉时对应字段为 None，理由见 reasons）。"""

    map: float | None = None
    sbp: float | None = None
    dbp: float | None = None
    hr: float | None = None
    dia_delta: float | None = None
    dia_flag: bool = False
    ipp_ratio: float | None = None
    arrhythmia: bool = False
    hr_flag: bool = False
    reasons: tuple[str, ...] = ()


def _refine_peak(pc: np.ndarray, amp: np.ndarray, idx: int) -> float:
    """抛物线顶点细化（3 点拟合）；边界或退化时回退采样点压力。"""
    if idx <= 0 or idx >= pc.size - 1:
        return float(pc[idx])
    x = pc[idx - 1:idx + 2]
    y = amp[idx - 1:idx + 2]
    if np.ptp(x) <= 0:
        return float(pc[idx])
    a, b, _ = np.polyfit(x, y, 2)
    if a >= 0:
        return float(pc[idx])
    xv = -b / (2.0 * a)
    if not (x[0] <= xv <= x[2]):
        return float(pc[idx])
    return float(xv)


def _first_crossing(pc: np.ndarray, amp: np.ndarray, level: float,
                    start: int, direction: int) -> float | None:
    """从 start 沿 direction 找 amp 首次跌破 level 的位置（线性插值压力）。"""
    n = pc.size
    i = start
    while 0 <= i + direction < n:
        j = i + direction
        y1, y2 = amp[i], amp[j]
        if y2 < level <= y1:
            if y1 == y2:
                return float(pc[j])
            return float(pc[i] + (level - y1) * (pc[j] - pc[i]) / (y2 - y1))
        i = j
    return None


def decide(env: Envelope, peak_times, params: AlgoParams | None = None) -> Decision:
    """包络 + 有效峰时刻 → Decision。"""
    params = params or AlgoParams()
    if env.n_points == 0:
        return Decision(reasons=("包络为空",))

    reasons: list[str] = []
    idx = int(np.argmax(env.amp_s))
    map_v = _refine_peak(env.pc, env.amp_s, idx)
    amax = float(env.amp_s[idx])

    sbp = _first_crossing(env.pc, env.amp_s, params.ratio_sbp * amax, idx, +1)
    if sbp is None:
        reasons.append("SBP 交叉未找到")
    dbp = _first_crossing(env.pc, env.amp_s, params.ratio_dbp * amax, idx, -1)
    if dbp is None:
        reasons.append("DBP 交叉未找到")

    dia_delta: float | None = None
    dia_flag = False
    if sbp is not None and dbp is not None:
        dia_formula = (3.0 * map_v - sbp) / 2.0
        dia_delta = float(abs(dbp - dia_formula))
        dia_flag = dia_delta > params.dia_tol

    hr: float | None = None
    ipp_ratio: float | None = None
    arrhythmia = False
    hr_flag = False
    times = np.asarray(peak_times, dtype=float)
    if times.size >= 2:
        rr = np.diff(times)
        med = float(np.median(rr))
        if med > 0:
            hr = 60.0 / med
            hr_flag = not (params.hr_min <= hr <= params.hr_max)
            ipp_ratio = float(np.mean(np.abs(rr - med) / med > params.ipp_thr))
            arrhythmia = bool(ipp_ratio > params.ipp_ratio_thr)

    return Decision(
        map=float(map_v), sbp=sbp, dbp=dbp, hr=hr,
        dia_delta=dia_delta, dia_flag=dia_flag,
        ipp_ratio=ipp_ratio, arrhythmia=arrhythmia, hr_flag=hr_flag,
        reasons=tuple(reasons),
    )
