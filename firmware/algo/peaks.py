"""逐搏峰检测移植：双门限滞回 + 动态梯度 + 两阶段有效性过滤。

与 ``algo/bp/peaks.py`` 语义一致：块梯度、峰距、prominence、邻搏一致性
与中文剔除原因均逐项对应。输入接受 list / array('i') / numpy 数组，
只做索引读取，不要求先转成元组列表。
"""

import math

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


class Candidate(object):
    def __init__(self, idx, t_s, val, prom, accepted, reason=""):
        self.idx = int(idx)
        self.t_s = float(t_s)
        self.val = _q(float(val))
        self.prom = _q(float(prom))
        self.accepted = bool(accepted)
        self.reason = reason


def _median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return _q(float(ordered[mid]))
    return _q(0.5 * (ordered[mid - 1] + ordered[mid]))


def _std(values):
    n = len(values)
    if n == 0:
        return 0.0
    if _QUANTIZER is None:
        total = 0.0
        total_sq = 0.0
        for value in values:
            total += value
            total_sq += value * value
        mean = total / n
        variance = total_sq / n - mean * mean
        if variance < 0.0:
            variance = 0.0
        return math.sqrt(variance)
    mean = _q(sum(values) / n)
    acc = 0.0
    for value in values:
        d = _q(value - mean)
        acc = _q(acc + _q(d * d))
    return _q(math.sqrt(acc / n))


def _gradient_blocks(ac, params, offset=0, stop=None):
    """按块计算梯度阈值，返回 ``(block, thresholds)``。

    阈值在每个块内为常数，因此只保留 ``n_blocks ≈ n/100`` 个值，
    避免旧实现的 n 长度 ``g`` 临时列表。``offset``/``stop`` 用于跳过两端
    margin，避免调用方复制裁剪数组。
    """
    end = len(ac) if stop is None else stop
    n = end - offset
    block = int(round(params.grad_block_s * params.fs))
    if block < 1:
        block = 1
    n_blocks = (n + block - 1) // block
    thresholds = [params.grad_min] * n_blocks
    for k in range(1, n_blocks):
        start = offset + (k - 1) * block
        prev = ac[start:min(offset + k * block, end)]
        if len(prev) < 2:
            continue
        gg = _q(params.grad_factor * _std(prev))
        thresholds[k] = gg if gg > params.grad_min else params.grad_min
    return block, thresholds


def _hysteresis(ac, block, thresholds, offset=0, stop=None):
    stop_index = len(ac) if stop is None else stop
    n = stop_index - offset
    if n <= 0:
        return []
    raw = []
    cur_max = ac[offset]
    max_idx = 0
    cur_min = ac[offset]
    prev_trough = ac[offset]
    seeking_peak = True
    for k in range(len(thresholds)):
        threshold = thresholds[k]
        if k == 0:
            start = 1
            block_end = block
        else:
            start = k * block
            block_end = (k + 1) * block
        if start >= n:
            break
        if block_end > n:
            block_end = n
        i = start
        for v in ac[offset + start:offset + block_end]:
            if seeking_peak:
                if v > cur_max:
                    cur_max = v
                    max_idx = i
                elif v < cur_max - threshold:
                    raw.append(
                        (
                            max_idx,
                            _q(float(cur_max)),
                            _q(float(cur_max - prev_trough)),
                        )
                    )
                    seeking_peak = False
                    cur_min = v
            else:
                if v < cur_min:
                    cur_min = v
                elif v > cur_min + threshold:
                    prev_trough = cur_min
                    seeking_peak = True
                    cur_max = v
                    max_idx = i
            i += 1
    return raw


def _apply_validity(raw, t_s, params):
    n = len(raw)
    out = [None] * n
    staged = []
    prom_win = []
    last_ok_idx = None

    for j in range(n):
        idx, val, prom = raw[j]
        reason = ""
        if last_ok_idx is not None:
            dt = _q((idx - last_ok_idx) / params.fs)
            if dt < params.rr_min_s:
                reason = "峰距过近"
        if not reason and len(prom_win) >= params.prom_arm_n:
            ref = _median(prom_win)
            if prom < _q(params.prom_ratio * ref):
                reason = "prominence 不足"
        if reason:
            out[j] = Candidate(idx, t_s[idx], val, prom, False, reason)
        else:
            staged.append(j)
            last_ok_idx = idx
            prom_win.append(prom)
            if len(prom_win) > params.prom_ref_n:
                prom_win.pop(0)

    for k in range(len(staged)):
        j = staged[k]
        idx, val, prom = raw[j]
        lo = k - params.lim_nbr
        if lo < 0:
            lo = 0
        hi = k + params.lim_nbr + 1
        if hi > len(staged):
            hi = len(staged)
        neigh = []
        for m in range(lo, hi):
            if m != k:
                neigh.append(raw[staged[m]][2])
        reason = ""
        if len(neigh) >= params.lim_nbr_min:
            ref = _median(neigh)
            deviation = _q(abs(prom - ref))
            if ref > 0.0 and _q(deviation / ref) > params.lim_dev:
                reason = "邻搏幅度离群"
        out[j] = Candidate(idx, t_s[idx], val, prom, not reason, reason)

    result = []
    for item in out:
        if item is not None:
            result.append(item)
    return result


def detect(ac, t_s, params=None, offset=0, stop=None):
    if params is None:
        params = AlgoParams()
    block, thresholds = _gradient_blocks(ac, params, offset, stop)
    raw = _hysteresis(ac, block, thresholds, offset, stop)
    return _apply_validity(raw, t_s, params)


def accepted_peaks(candidates):
    result = []
    for candidate in candidates:
        if candidate.accepted:
            result.append(candidate)
    return result
