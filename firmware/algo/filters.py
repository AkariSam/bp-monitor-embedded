"""数字滤波移植：2 阶 Butterworth DC/AC + 因果 SOS + 时基补偿。

PC 正本 ``algo/bp/filters.py`` 使用 scipy.signal.butter/sosfilt/sosfreqz。
固件侧以纯 Python 重建同一 2 阶 Butterworth（Q=1/sqrt(2)）的 RBJ/bilinear
系数；SOS 采用 scipy 相同的 DF2T 级联与 ``sosfilt_zi`` 稳态初始化。
当前冻结参数为 2 阶；若修改 order 会显式报错，避免静默偏离正本。
"""

import math
from array import array


SQRT2 = 1.4142135623730951

_F_ITEMSIZE = 4
try:
    _F_ITEMSIZE = array("f").itemsize
except AttributeError:
    pass

# 测试/敏感性分析钩子：默认 None，不做任何量化；S4 测试可注入 float32 往返。
_QUANTIZER = None


def set_quantizer(func):
    global _QUANTIZER
    _QUANTIZER = func


def _q(value):
    q = _QUANTIZER
    if q is None:
        return value
    return q(value)


def _check_cutoff(fc, fs):
    if not (0.0 < fc < 0.5 * fs):
        raise ValueError("截止频率必须在 (0, fs/2) 内")


def _lowpass2(fc, fs):
    _check_cutoff(fc, fs)
    w0 = 2.0 * math.pi * fc / fs
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = sw / SQRT2
    b0 = (1.0 - cw) * 0.5
    b1 = 1.0 - cw
    b2 = b0
    a0 = 1.0 + alpha
    a1 = -2.0 * cw
    a2 = 1.0 - alpha
    return [
        _q(b0 / a0), _q(b1 / a0), _q(b2 / a0),
        _q(1.0), _q(a1 / a0), _q(a2 / a0),
    ]


def _highpass2(fc, fs):
    _check_cutoff(fc, fs)
    w0 = 2.0 * math.pi * fc / fs
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = sw / SQRT2
    b0 = (1.0 + cw) * 0.5
    b1 = -(1.0 + cw)
    b2 = b0
    a0 = 1.0 + alpha
    a1 = -2.0 * cw
    a2 = 1.0 - alpha
    return [
        _q(b0 / a0), _q(b1 / a0), _q(b2 / a0),
        _q(1.0), _q(a1 / a0), _q(a2 / a0),
    ]


def design_dc(params):
    if params.dc_order != 2:
        raise ValueError("firmware 滤波器移植当前仅支持 2 阶 Butterworth")
    return [_lowpass2(params.dc_cut_hz, params.fs)]


def design_ac(params):
    if params.ac_hp_order != 2 or params.ac_lp_order != 2:
        raise ValueError("firmware 滤波器移植当前仅支持 2 阶 Butterworth")
    return [
        _highpass2(params.ac_hp_hz, params.fs),
        _lowpass2(params.ac_lp_hz, params.fs),
    ]


def sosfilt_zi(sos):
    """单位阶跃输入的稳态状态，语义同 scipy.signal.sosfilt_zi。"""
    zi = []
    x = _q(1.0)
    for section in sos:
        b0, b1, b2, a0, a1, a2 = section
        b0 = _q(b0 / a0)
        b1 = _q(b1 / a0)
        b2 = _q(b2 / a0)
        a1 = _q(a1 / a0)
        a2 = _q(a2 / a0)
        den = _q(1.0 + a1 + a2)
        if den == 0.0:
            y = 0.0
        else:
            y = _q((b0 + b1 + b2) / den * x)
        z1 = _q(b2 * x - a2 * y)
        z0 = _q(y - b0 * x)
        zi.append([z0, z1])
        x = y
    return zi


def causal_filter(sos, x):
    """因果 SOS 滤波，DF2T；按首样本稳态初始化（同 PC 正本）。"""
    n = len(x)
    if n == 0:
        return []
    x0 = _q(x[0])
    zi = sosfilt_zi(sos)
    inp = x
    for s in range(len(sos)):
        b0, b1, b2, a0, a1, a2 = sos[s]
        b0 = _q(b0 / a0)
        b1 = _q(b1 / a0)
        b2 = _q(b2 / a0)
        a1 = _q(a1 / a0)
        a2 = _q(a2 / a0)
        z0 = _q(zi[s][0] * x0)
        z1 = _q(zi[s][1] * x0)
        out = [0.0] * n
        for i in range(n):
            xi = _q(inp[i])
            yi = _q(b0 * xi + z0)
            z0 = _q(b1 * xi - a1 * yi + z1)
            z1 = _q(b2 * xi - a2 * yi)
            out[i] = yi
        inp = out
    return inp


def compensate(x, delay_samples):
    """按延迟左移（正）或右移（负）；移出端补零。"""
    n = len(x)
    out = [0.0] * n
    d = int(round(delay_samples))
    if d == 0:
        for i in range(n):
            out[i] = _q(x[i])
        return out
    if d > 0:
        if d >= n:
            return out
        for i in range(n - d):
            out[i] = _q(x[i + d])
        return out
    d = -d
    if d >= n:
        return out
    for i in range(n - d):
        out[i + d] = _q(x[i])
    return out


def f32_array(values):
    """构造与输入同长的 ``array('f')``（设备分析器的工作缓冲区）。"""
    n = len(values)
    out = array("f", bytes(_F_ITEMSIZE * n))
    for i in range(n):
        out[i] = values[i]
    return out


def normalized_coeffs(section):
    """返回归一化后的 ``(b0, b1, b2, a1, a2)``（a0 = 1）。"""
    b0, b1, b2, a0, a1, a2 = section
    return (
        _q(b0 / a0),
        _q(b1 / a0),
        _q(b2 / a0),
        _q(a1 / a0),
        _q(a2 / a0),
    )


def filter_section_inplace(values, coeffs, zi, state, start, stop):
    """在 ``array('f')`` 上就地进行单级 DF2T 滤波。

    ``state`` 为 ``None`` 时按 ``values[0]`` 做稳态初始化；返回新的
    ``[z0, z1]``，供分段处理时续接。语义与 ``causal_filter`` 的单级一致，
    只是每步输出写回原数组（float32 存储）。生产路径（未装 quantizer）
    走无 ``_q`` 调用的快速循环，S4 敏感性测试才走量化分支。
    """
    b0, b1, b2, a1, a2 = coeffs
    if start >= stop:
        return state
    if state is None:
        x0 = values[0]
        z0 = zi[0] * x0
        z1 = zi[1] * x0
    else:
        z0, z1 = state
    q = _QUANTIZER
    if q is None:
        for i in range(start, stop):
            xi = values[i]
            yi = b0 * xi + z0
            z0 = b1 * xi - a1 * yi + z1
            z1 = b2 * xi - a2 * yi
            values[i] = yi
    else:
        z0 = q(z0)
        z1 = q(z1)
        for i in range(start, stop):
            xi = q(values[i])
            yi = q(b0 * xi + z0)
            z0 = q(b1 * xi - a1 * yi + z1)
            z1 = q(b2 * xi - a2 * yi)
            values[i] = yi
    return [z0, z1]


def compensate_inplace(values, delay_samples):
    """按延迟原地左移（正）或右移（负）；移出端补零。"""
    n = len(values)
    d = int(round(delay_samples))
    if d == 0:
        return values
    if d > 0:
        if d >= n:
            for i in range(n):
                values[i] = 0.0
            return values
        for i in range(n - d):
            values[i] = values[i + d]
        for i in range(n - d, n):
            values[i] = 0.0
        return values
    d = -d
    if d >= n:
        for i in range(n):
            values[i] = 0.0
        return values
    for i in range(n - 1, d - 1, -1):
        values[i] = values[i - d]
    for i in range(d):
        values[i] = 0.0
    return values


def _section_response(section, w):
    b0, b1, b2, a0, a1, a2 = section
    c1 = _q(math.cos(w))
    s1 = _q(math.sin(w))
    c2 = _q(math.cos(2.0 * w))
    s2 = _q(math.sin(2.0 * w))
    nr = _q(b0 + _q(b1 * c1) + _q(b2 * c2))
    ni = _q(-(_q(b1 * s1) + _q(b2 * s2)))
    dr = _q(a0 + _q(a1 * c1) + _q(a2 * c2))
    di = _q(-(_q(a1 * s1) + _q(a2 * s2)))
    den = _q(dr * dr + di * di)
    if den == 0.0:
        return (0.0, 0.0)
    return (
        _q((nr * dr + ni * di) / den),
        _q((ni * dr - nr * di) / den),
    )


def _response(sos, w):
    hr = 1.0
    hi = 0.0
    for section in sos:
        sr, si = _section_response(section, w)
        tr = _q(hr * sr - hi * si)
        ti = _q(hr * si + hi * sr)
        hr = tr
        hi = ti
    return hr, hi


def _unwrap(phases):
    out = [phases[0]]
    prev = phases[0]
    for k in range(1, len(phases)):
        p = _q(phases[k])
        while p - prev > math.pi:
            p = _q(p - 2.0 * math.pi)
        while p - prev < -math.pi:
            p = _q(p + 2.0 * math.pi)
        out.append(_q(p))
        prev = p
    return out


def _linear_slope(xs, ys):
    n = len(xs)
    mx = _q(sum(xs) / n)
    my = _q(sum(ys) / n)
    num = 0.0
    den = 0.0
    for i in range(n):
        dx = _q(xs[i] - mx)
        dy = _q(ys[i] - my)
        num = _q(num + _q(dx * dy))
        den = _q(den + _q(dx * dx))
    if den == 0.0:
        return 0.0
    return _q(num / den)


def _interp(x, xs, ys):
    if x <= xs[0]:
        return _q(ys[0])
    if x >= xs[-1]:
        return _q(ys[-1])
    for i in range(1, len(xs)):
        if x <= xs[i]:
            span = xs[i] - xs[i - 1]
            if span == 0.0:
                return _q(ys[i])
            f = _q((x - xs[i - 1]) / span)
            return _q(ys[i - 1] + f * (ys[i] - ys[i - 1]))
    return _q(ys[-1])


def _phase_at(sos, ws):
    phases = []
    for w in ws:
        hr, hi = _response(sos, w)
        phases.append(_q(math.atan2(hi, hr)))
    return _unwrap(phases)


def group_delay_samples(sos, fs, f_ref_hz):
    w_ref = 2.0 * math.pi * f_ref_hz / fs
    dw = max(0.02 * w_ref, 1e-4)
    ws = [w_ref - 2.0 * dw, w_ref - dw, w_ref, w_ref + dw, w_ref + 2.0 * dw]
    phase = _phase_at(sos, ws)
    return _q(-_linear_slope(ws, phase))


def phase_delay_samples(sos, fs, f_ref_hz):
    w_ref = 2.0 * math.pi * f_ref_hz / fs
    dw = max(0.02 * w_ref, 1e-4)
    ws = [w_ref - 2.0 * dw, w_ref - dw, w_ref, w_ref + dw, w_ref + 2.0 * dw]
    phase = _phase_at(sos, ws)
    phase_ref = _interp(w_ref, ws, phase)
    return _q(-phase_ref / w_ref)


def filter_dc_ac(p, params=None):
    if params is None:
        from algo.params import AlgoParams

        params = AlgoParams()
    sos_dc = design_dc(params)
    sos_ac = design_ac(params)
    dc = causal_filter(sos_dc, p)
    ac = causal_filter(sos_ac, p)
    dc = compensate(
        dc, group_delay_samples(sos_dc, params.fs, params.gd_dc_ref_hz)
    )
    ac = compensate(
        ac, phase_delay_samples(sos_ac, params.fs, params.gd_ac_ref_hz)
    )
    return dc, ac
