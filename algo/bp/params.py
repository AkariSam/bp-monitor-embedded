"""算法参数集中管理（单一来源）。

所有可调参数收敛到 ``AlgoParams``：滤波器、峰检测、包络、判定、质控。
默认值出自《技术实现计划 v2.2》§4.2–4.4；用 ``dataclasses.replace`` 覆盖单项，
保证「默认行为恒定、实验可追溯」。本表也是未来固件 Flash 参数表的原型。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AlgoParams:
    """PC 管线全部算法参数（默认值 = 规格初值）。"""

    # ---- 滤波器（§4.2）----
    fs: float = 80.0              # 采样率 [Hz]（W5 契约 80 Hz）
    dc_order: int = 2             # DC 低通阶数
    dc_cut_hz: float = 0.5        # DC 低通截止 [Hz]
    ac_hp_order: int = 2          # AC 高通阶数（与 LP 级联 = 4 阶带通）
    ac_hp_hz: float = 0.5         # AC 带通下限 [Hz]
    ac_lp_order: int = 2          # AC 低通阶数
    ac_lp_hz: float = 8.0         # AC 带通上限 [Hz]
    gd_ac_ref_hz: float = 1.2     # AC 群延迟参考频率 [Hz]（≈HR72 脉搏基频）
    gd_dc_ref_hz: float = 0.1     # DC 群延迟参考频率 [Hz]（放气斜坡主频带）
    edge_margin_s: float = 0.5    # DEFLATE 切片两端分析余量 [s]

    # ---- 峰检测（§4.3）----
    grad_block_s: float = 1.25    # 块统计时长 [s]（GCY 100 采样 @ 其采样率 ≈1.25 s；按 fs 折算采样数）
    grad_factor: float = 1.0      # 梯度 = grad_factor × 块标准差（GCY 归约，见 docs/pipeline/peaks.md）
    grad_min: float = 0.5         # 梯度下限 [mmHg]（静默区防噪底误检，仿真实测标定）
    prom_ratio: float = 0.25      # prominence ≥ prom_ratio × 近期峰幅中位
    prom_ref_n: int = 10          # 近期峰幅窗口 [峰数]
    prom_arm_n: int = 3           # 门控启用前最少样本 [峰数]
    rr_min_s: float = 0.3         # 峰距下限 [s]（防双峰，300 ms 规格）
    rr_max_s: float = 1.5         # 峰距参考上限 [s]（HR 40 对应；仅标记不硬剔）
    lim_dev: float = 0.5          # 邻搏幅度相对中位偏差上限（>50% 剔除）
    lim_nbr: int = 1              # 邻搏一致性中心窗：前后各取邻居数 [搏]
    lim_nbr_min: int = 2          # 邻搏参考最少邻居数（不足则门控未启用）

    # ---- 包络（§4.3）----
    env_median: int = 3           # 中值滤波窗口 [点]
    env_ma: int = 3               # 滑动平均窗口 [点]
    env_min_points: int = 8       # 包络最少点数（<8 判废）

    # ---- 判定（§4.4）----
    ratio_sbp: float = 0.50       # SBP 比率（Flash 可调 0.40–0.65）
    ratio_dbp: float = 0.70       # DBP 比率（Flash 可调 0.60–0.85）
    dia_tol: float = 10.0         # DIA 交叉校验容差 [mmHg]
    ipp_thr: float = 0.2          # 单次峰间期离群阈值（相对中位）
    ipp_ratio_thr: float = 0.2    # 不规则占比阈值 → 心律不齐标志
    hr_min: float = 40.0          # 心率窗下限 [bpm]
    hr_max: float = 180.0         # 心率窗上限 [bpm]

    # ---- 质控（§4.4）----
    env_cv_max: float = 0.35      # 包络残差 CV 上限（粗糙度，> 判废）
    outlier_ratio_max: float = 0.3  # Lim 离群峰占比上限（> 判废）
