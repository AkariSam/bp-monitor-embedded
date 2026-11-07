"""信号模型：动脉顺应性 C(Pt) → 振荡包络；袖带 DC 四段斜坡；脉搏波形合成。

顺应性采用 B+μ 非对称高斯族（C¹ 光滑，erf 解析积分），三个形状参数对齐
信号处理端锚点（r_s=0.50 / r_d=0.70 / k=0.41），推导与出处见 ``docs/sim/model.md``。

两级时间网格：本模块在 fs_sim=1000 Hz 的密集参考网格上合成"连续过程"，
由 ``sim.generate`` 抽样到 80 Hz 输出网格。
"""

from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
from scipy.special import erf

# ---- 全局常量（与硬件/契约绑定，全项目唯一）----
FS_SIM = 1000.0  # 内部密集参考网格采样率 [Hz]
FS_OUT = 80.0    # 输出采样率 [Hz]（与固件采集器契约一致）
LSB = 0.1        # ADC 量化台阶 [mmHg]（对齐 E1 分辨率规格）


@dataclass(frozen=True)
class PhysioParams:
    """生理与气路参数；默认值 = normal 场景基线。"""

    sbp: float = 120.0
    dbp: float = 80.0
    hr: float = 70.0
    sigma_n_r: float = 0.05842820    # σₙ/PP：低谷侧（压闭）高斯宽度
    sigma_p_r: float = 0.78058178    # σₚ/PP：高峰侧（开放）高斯宽度
    mu_r: float = -0.34036045        # μ/PP：顺应性峰偏移（负=轻度压闭侧）
    v_max: float = 50.0          # 血容积 S 曲线幅值 [a.u.]
    k_cuff: float = 20.0         # 气路转换系数（ΔV → ΔP 的除数）[a.u./mmHg]
    beat_jitter: float = 0.0     # RR 间期抖动比例（0=规则；房颤用 0.15~0.3）
    p_target: float = 180.0      # 充气目标 [mmHg]
    p_deflate_end: float = 40.0  # 放气段结束压力 [mmHg]
    inflate_rate: float = 10.0   # 充气速率 [mmHg/s]
    deflate_rate: float = 3.0    # 放气速率 [mmHg/s]（设计要求 2–5）
    dump_rate: float = 80.0      # 快排速率 [mmHg/s]
    settle_s: float = 1.0        # 停泵静置时长 [s]（丢弃瞬态用）

    @property
    def pp(self) -> float:
        """脉压 [mmHg]。"""
        return self.sbp - self.dbp

    def resolved_shape(self) -> tuple[float, float, float]:
        """实际使用的形状参数 [mmHg]：按脉压缩放 (σₙ, σₚ, μ)。"""
        pp = self.pp
        return self.sigma_n_r * pp, self.sigma_p_r * pp, self.mu_r * pp

    @property
    def map_model(self) -> float:
        """包络峰值的数值解 [mmHg]（非对称形状下不再是 (SBP+DBP)/2）。"""
        pc = np.linspace(self.dbp - 60.0, self.sbp + 60.0, 24001)
        a = beat_amplitude(pc, self)
        i = int(np.argmax(a))
        if 0 < i < pc.size - 1:
            y0, y1, y2 = a[i - 1], a[i], a[i + 1]
            denom = y0 - 2.0 * y1 + y2
            if abs(denom) > 1e-30:
                delta = 0.5 * (y0 - y2) / denom
                return float(pc[i] + delta * (pc[1] - pc[0]))
        return float(pc[i])


class SimSignal(NamedTuple):
    """一次合成的密集网格结果。"""

    t: np.ndarray        # 时间 [s]
    p_clean: np.ndarray  # 干净袖带压 [mmHg]
    phase: np.ndarray    # 相位字符串（INFLATE/SETTLE/DEFLATE/DUMP）
    beats: np.ndarray    # 搏动起点时刻 [s]


def volume(pt, v_max: float, sigma_n: float, sigma_p: float,
           mu: float) -> np.ndarray:
    """血容积 V(Pt)：非对称高斯顺应性 C(Pt) 的积分（erf 解析式）。

    C(Pt) = C₀·exp(−(Pt−μ)²/(2σ(Pt)²))，σ 在峰位 μ 处分为 σₙ（Pt<μ）/
    σₚ（Pt≥μ）；分片点取在峰位，保证 C 值与一阶导数均连续（C¹ 光滑）。
    """
    u = np.atleast_1d(np.asarray(pt, dtype=float)) - mu
    rt = np.sqrt(np.pi / 2.0)
    c0 = v_max / (rt * (sigma_n + sigma_p))
    out = np.empty_like(u)
    neg = u < 0
    out[neg] = c0 * rt * sigma_n * (1.0 + erf(u[neg] / (np.sqrt(2.0) * sigma_n)))
    out[~neg] = c0 * rt * (sigma_n + sigma_p * erf(u[~neg]
                                                 / (np.sqrt(2.0) * sigma_p)))
    return out


def beat_amplitude(pc, params: PhysioParams):
    """准静态积分给出的逐搏振荡幅度 [mmHg]：

    A(Pc) = [V(SBP−Pc) − V(DBP−Pc)] / k_cuff
    """
    sn, sp, mu = params.resolved_shape()
    return (volume(params.sbp - pc, params.v_max, sn, sp, mu)
            - volume(params.dbp - pc, params.v_max, sn, sp, mu)) / params.k_cuff


def pulse_wave(phi) -> np.ndarray:
    """归一化脉搏波形（双高斯：收缩主峰 + 重搏波），phi ∈ [0,1) 为一搏内相位。"""
    phi = np.asarray(phi, dtype=float)
    main = np.exp(-(((phi - 0.15) / 0.08) ** 2))
    dicrotic = 0.4 * np.exp(-(((phi - 0.35) / 0.10) ** 2))
    return main + dicrotic


def dc_ramp(params: PhysioParams, fs: float = FS_SIM):
    """四段 DC 斜坡（INFLATE/SETTLE/DEFLATE/DUMP），返回 (t [s], p [mmHg], phase)。"""
    t_inf = params.p_target / params.inflate_rate
    t_def_start = t_inf + params.settle_s
    t_def = (params.p_target - params.p_deflate_end) / params.deflate_rate
    t_dmp_start = t_def_start + t_def
    t_dmp = params.p_deflate_end / params.dump_rate
    n = int(round((t_dmp_start + t_dmp) * fs))
    t = np.arange(n) / fs
    p = np.empty(n)
    phase = np.empty(n, dtype="<U7")

    m_inf = t < t_inf
    m_set = (t >= t_inf) & (t < t_def_start)
    m_def = (t >= t_def_start) & (t < t_dmp_start)
    m_dmp = ~(m_inf | m_set | m_def)

    p[m_inf] = t[m_inf] * params.inflate_rate
    p[m_set] = params.p_target
    p[m_def] = params.p_target - (t[m_def] - t_def_start) * params.deflate_rate
    p[m_dmp] = np.maximum(
        params.p_deflate_end - (t[m_dmp] - t_dmp_start) * params.dump_rate, 0.0)

    phase[m_inf] = "INFLATE"
    phase[m_set] = "SETTLE"
    phase[m_def] = "DEFLATE"
    phase[m_dmp] = "DUMP"
    return t, p, phase


def beat_times(params: PhysioParams, duration: float,
               rng: np.random.Generator) -> np.ndarray:
    """搏动起点时刻序列 [s]；beat_jitter>0 时 RR 间期随机化（房颤场景）。"""
    mean_rr = 60.0 / params.hr
    times = [0.0]
    t = 0.0
    while True:
        rr = mean_rr
        if params.beat_jitter > 0.0:
            rr *= max(1.0 + params.beat_jitter * rng.standard_normal(), 0.25)
        t += rr
        if t >= duration:
            break
        times.append(t)
    return np.array(times)


def synthesize(params: PhysioParams, fs: float = FS_SIM,
               rng: np.random.Generator | None = None) -> SimSignal:
    """在密集网格上合成干净袖带压 p_clean(t)。"""
    if rng is None:
        rng = np.random.default_rng(0)
    t, pc, phase = dc_ramp(params, fs)
    p_clean = pc.copy()
    beats = beat_times(params, t[-1], rng)
    n = t.size
    for i, tb in enumerate(beats):
        tb_next = beats[i + 1] if i + 1 < beats.size else t[-1]
        if tb >= t[-1]:
            break
        lo = int(np.ceil(tb * fs))
        hi = min(int(np.ceil(tb_next * fs)), n)
        if hi <= lo:
            continue
        # 准静态 + 搏内调制：幅度随袖带压逐样本变化 A(Pc(t))，不设人为取幅点
        # （修正原"搏动中点单点取幅"约定：真实设备只能观测同刻压力，见 model.md §5.2）
        phi = (t[lo:hi] - tb) / (tb_next - tb)
        p_clean[lo:hi] += beat_amplitude(pc[lo:hi], params) * pulse_wave(phi)
    return SimSignal(t=t, p_clean=p_clean, phase=phase, beats=beats)
