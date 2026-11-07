"""包络构建移植：逐搏 (袖带压, 振幅) → 排序 → 中值 + 滑动平均。"""

_QUANTIZER = None


def set_quantizer(func):
    global _QUANTIZER
    _QUANTIZER = func


def _q(value):
    q = _QUANTIZER
    if q is None:
        return value
    return q(value)


class Envelope(object):
    def __init__(self, pc, amp, amp_s):
        self.pc = pc
        self.amp = amp
        self.amp_s = amp_s

    @property
    def n_points(self):
        return len(self.pc)


def _median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return _q(float(ordered[mid]))
    return _q(0.5 * (ordered[mid - 1] + ordered[mid]))


def _clamp(i, n):
    if i < 0:
        return 0
    if i >= n:
        return n - 1
    return i


def smooth(amp, median_w=3, ma_w=3):
    """中值 + 滑动平均，nearest 边缘模式（与 PC scipy 调用一致）。"""
    n = len(amp)
    if n == 0:
        return []
    y = [_q(float(v)) for v in amp]

    if median_w >= 2:
        left = median_w // 2
        right = median_w - left - 1
        out = [0.0] * n
        for i in range(n):
            window = []
            for k in range(i - left, i + right + 1):
                window.append(y[_clamp(k, n)])
            out[i] = _q(_median(window))
        y = out

    if ma_w >= 2:
        left = ma_w // 2
        right = ma_w - left - 1
        out = [0.0] * n
        for i in range(n):
            total = 0.0
            for k in range(i - left, i + right + 1):
                total = _q(total + y[_clamp(k, n)])
            out[i] = _q(total / ma_w)
        y = out

    return y


def build(pc, amp, params=None):
    if params is None:
        from algo.params import AlgoParams

        params = AlgoParams()
    pairs = list(zip(pc, amp))
    pairs.sort(key=lambda item: item[0])
    pc_sorted = [_q(float(item[0])) for item in pairs]
    amp_sorted = [_q(float(item[1])) for item in pairs]
    return Envelope(
        pc_sorted,
        amp_sorted,
        smooth(amp_sorted, params.env_median, params.env_ma),
    )


def from_peaks(dc, ac, peaks):
    idxs = []
    for peak in peaks:
        idxs.append(int(peak.idx))
    n = len(ac)
    m = len(idxs)
    if m == 0:
        return [], []

    mids = []
    for i in range(m - 1):
        mids.append((idxs[i] + idxs[i + 1]) // 2)

    pc_list = []
    amp_list = []
    for i in range(m):
        pk = idxs[i]
        if m == 1:
            lo = 0
            hi = n
        else:
            if i == 0:
                lo = pk - (idxs[1] - pk) // 2
                if lo < 0:
                    lo = 0
                hi = mids[0]
            elif i == m - 1:
                lo = mids[i - 1]
                hi = pk + (pk - idxs[i - 1]) // 2
                if hi > n:
                    hi = n
            else:
                lo = mids[i - 1]
                hi = mids[i]
        if hi <= lo:
            continue
        segment = ac[lo:hi]
        pc_list.append(_q(float(dc[pk])))
        amp_list.append(_q(float(max(segment) - min(segment))))
    return pc_list, amp_list
