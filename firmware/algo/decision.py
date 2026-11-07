"""判定移植：MAP / SBP / DBP / HR 与 DIA 交叉校验。"""

from algo.params import AlgoParams

_QUANTIZER = None


def set_quantizer(func):
    global _QUANTIZER
    _QUANTIZER = func


def _q(value):
    q = _QUANTIZER
    if q is None:
        return value
    return q(value)


class Decision(object):
    def __init__(
        self,
        map=None,
        sbp=None,
        dbp=None,
        hr=None,
        dia_delta=None,
        dia_flag=False,
        ipp_ratio=None,
        arrhythmia=False,
        hr_flag=False,
        reasons=(),
    ):
        self.map = None if map is None else _q(map)
        self.sbp = None if sbp is None else _q(sbp)
        self.dbp = None if dbp is None else _q(dbp)
        self.hr = None if hr is None else _q(hr)
        self.dia_delta = None if dia_delta is None else _q(dia_delta)
        self.dia_flag = bool(dia_flag)
        self.ipp_ratio = None if ipp_ratio is None else _q(ipp_ratio)
        self.arrhythmia = bool(arrhythmia)
        self.hr_flag = bool(hr_flag)
        self.reasons = tuple(reasons)


def _median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return _q(float(ordered[mid]))
    return _q(0.5 * (ordered[mid - 1] + ordered[mid]))


def _refine_peak(pc, amp, idx):
    if idx <= 0 or idx >= len(pc) - 1:
        return _q(float(pc[idx]))
    x0 = _q(pc[idx - 1])
    x1 = _q(pc[idx])
    x2 = _q(pc[idx + 1])
    y0 = _q(amp[idx - 1])
    y1 = _q(amp[idx])
    y2 = _q(amp[idx + 1])
    if max(x0, x1, x2) - min(x0, x1, x2) <= 0.0:
        return _q(float(pc[idx]))
    if x1 == x0 or x2 == x1 or x2 == x0:
        # 重复压力点使三点二次插值退化；与 PTF 的采样点回退一致。
        return _q(float(pc[idx]))
    d01 = _q((y1 - y0) / (x1 - x0))
    d12 = _q((y2 - y1) / (x2 - x1))
    a = _q((d12 - d01) / (x2 - x0))
    if a >= 0.0:
        return _q(float(pc[idx]))
    b = _q(d01 - a * (x0 + x1))
    xv = _q(-b / (2.0 * a))
    if not (x0 <= xv <= x2):
        return _q(float(pc[idx]))
    return _q(float(xv))


def _first_crossing(pc, amp, level, start, direction):
    n = len(pc)
    i = start
    level = _q(level)
    while 0 <= i + direction < n:
        j = i + direction
        y1 = _q(amp[i])
        y2 = _q(amp[j])
        if y2 < level <= y1:
            if y1 == y2:
                return _q(float(pc[j]))
            return _q(
                float(pc[i] + (level - y1) * (pc[j] - pc[i]) / (y2 - y1))
            )
        i = j
    return None


def decide(env, peak_times, params=None):
    if params is None:
        params = AlgoParams()
    if env.n_points == 0:
        return Decision(reasons=("包络为空",))

    reasons = []
    idx = 0
    amax = _q(env.amp_s[0])
    for i in range(1, env.n_points):
        value = _q(env.amp_s[i])
        if value > amax:
            amax = value
            idx = i
    map_v = _refine_peak(env.pc, env.amp_s, idx)

    sbp = _first_crossing(
        env.pc, env.amp_s, _q(params.ratio_sbp * amax), idx, 1
    )
    if sbp is None:
        reasons.append("SBP 交叉未找到")
    dbp = _first_crossing(
        env.pc, env.amp_s, _q(params.ratio_dbp * amax), idx, -1
    )
    if dbp is None:
        reasons.append("DBP 交叉未找到")

    dia_delta = None
    dia_flag = False
    if sbp is not None and dbp is not None:
        dia_formula = _q((3.0 * map_v - sbp) / 2.0)
        dia_delta = _q(abs(dbp - dia_formula))
        dia_flag = dia_delta > params.dia_tol

    hr = None
    ipp_ratio = None
    arrhythmia = False
    hr_flag = False
    if len(peak_times) >= 2:
        rr = []
        for i in range(1, len(peak_times)):
            rr.append(_q(peak_times[i] - peak_times[i - 1]))
        med = _median(rr)
        if med > 0.0:
            hr = _q(60.0 / med)
            hr_flag = not (params.hr_min <= hr <= params.hr_max)
            n_out = 0
            for interval in rr:
                deviation = _q(abs(interval - med))
                if _q(deviation / med) > params.ipp_thr:
                    n_out += 1
            ipp_ratio = _q(n_out / len(rr))
            arrhythmia = ipp_ratio > params.ipp_ratio_thr

    return Decision(
        map=float(map_v),
        sbp=sbp,
        dbp=dbp,
        hr=hr,
        dia_delta=dia_delta,
        dia_flag=dia_flag,
        ipp_ratio=ipp_ratio,
        arrhythmia=arrhythmia,
        hr_flag=hr_flag,
        reasons=tuple(reasons),
    )
