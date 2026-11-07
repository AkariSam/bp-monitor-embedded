"""数字滤波：DC 低通 / AC 带通设计与因果滤波、群延迟补偿（§4.2）。

设计要点（与固件移植语义一致）：
- **因果 IIR**（``sosfilt``）：固件将执行同一套 biquad；PC 端不使用 filtfilt。
- **启动初始化**：``sosfilt_zi`` 按首样本稳态初始化，消除 0.5 Hz 低切
  滤波器的长启动暂态（否则 DEFLATE 段开头会残留数 mmHg 的假振荡）。
- **群延迟补偿**：在参考频率处数值测量群延迟（采样），滤波后将信号左移
  对应采样数，使峰时刻与袖带压对齐（agata 83 采样未补偿错位教训）。
  补偿后信号不再是严格因果序列（仅分析用，固件侧同样只影响包络配对时刻）。
"""

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi, sosfreqz

from .params import AlgoParams


def design_dc(params: AlgoParams) -> np.ndarray:
    """DC 路：2 阶低通（默认 0.5 Hz），返回 sos。"""
    return butter(
        params.dc_order, params.dc_cut_hz, btype="lowpass",
        fs=params.fs, output="sos",
    )


def design_ac(params: AlgoParams) -> np.ndarray:
    """AC 路：2 阶高通 + 2 阶低通级联（= 4 阶带通 0.5–8 Hz），返回 sos。"""
    hp = butter(
        params.ac_hp_order, params.ac_hp_hz, btype="highpass",
        fs=params.fs, output="sos",
    )
    lp = butter(
        params.ac_lp_order, params.ac_lp_hz, btype="lowpass",
        fs=params.fs, output="sos",
    )
    return np.vstack([hp, lp])


def group_delay_samples(sos: np.ndarray, fs: float, f_ref_hz: float) -> float:
    """在参考频率处数值测量群延迟，单位：采样。

    用 sosfreqz 在 f_ref 附近 5 点采样、对解卷绕相位做局部线性拟合。
    适用于慢变信号（DC 放气斜坡）的时基对齐。
    """
    w_ref = 2.0 * np.pi * f_ref_hz / fs
    dw = max(0.02 * w_ref, 1e-4)
    ws = w_ref + np.linspace(-2.0 * dw, 2.0 * dw, 5)
    _, h = sosfreqz(sos, worN=ws)
    phase = np.unwrap(np.angle(h))
    slope = np.polyfit(ws, phase, 1)[0]
    return float(-slope)


def phase_delay_samples(sos: np.ndarray, fs: float, f_ref_hz: float) -> float:
    """在参考频率处测量相位延迟，单位：采样（正 = 滞后，负 = 超前）。

    适用于窄带特征（AC 脉搏峰）的时基对齐：峰的移动量由基频处的相位
    决定，而非群延迟。4 阶带通在 0.5–8 Hz 内相位先超后滞后，
    1.2 Hz 处实测约为负值（超前），必须以相位延迟补偿。
    """
    w_ref = 2.0 * np.pi * f_ref_hz / fs
    dw = max(0.02 * w_ref, 1e-4)
    ws = w_ref + np.linspace(-2.0 * dw, 2.0 * dw, 5)
    _, h = sosfreqz(sos, worN=ws)
    phase = np.unwrap(np.angle(h))
    phase_ref = float(np.interp(w_ref, ws, phase))
    return float(-phase_ref / w_ref)


def compensate(x: np.ndarray, delay_samples: float) -> np.ndarray:
    """按延迟左移信号（负延迟则右移）实现时基对齐；移出端补零。"""
    x = np.asarray(x, dtype=float)
    d = int(round(delay_samples))
    out = np.zeros_like(x)
    if d == 0:
        return x.copy()
    if d > 0:
        if d >= x.size:
            return out
        out[:-d] = x[d:]
    else:
        d = -d
        if d >= x.size:
            return out
        out[d:] = x[:-d]
    return out


def causal_filter(sos: np.ndarray, x: np.ndarray) -> np.ndarray:
    """因果 IIR 滤波（固件同语义）；按首样本稳态初始化抑制启动暂态。"""
    x = np.asarray(x, dtype=float)
    zi = sosfilt_zi(sos) * x[0]
    y, _ = sosfilt(sos, x, zi=zi)
    return y


def filter_dc_ac(p: np.ndarray, params: AlgoParams | None = None):
    """袖带压序列 → (dc, ac)：设计、因果滤波、时基对齐一步完成。

    DC 用群延迟（斜坡时基）、AC 用基频相位延迟（峰时刻对齐）。
    """
    params = params or AlgoParams()
    sos_dc = design_dc(params)
    sos_ac = design_ac(params)
    dc = causal_filter(sos_dc, p)
    ac = causal_filter(sos_ac, p)
    dc = compensate(dc, group_delay_samples(sos_dc, params.fs, params.gd_dc_ref_hz))
    ac = compensate(ac, phase_delay_samples(sos_ac, params.fs, params.gd_ac_ref_hz))
    return dc, ac
