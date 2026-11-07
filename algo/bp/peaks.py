"""逐搏峰检测：双门限滞回 + 动态梯度 + 有效性后处理（§4.3）。

算法（与固件移植语义一致，O(1) 内存）：

1. **双门限滞回**（GCY ``PeakDetect`` 同机制）：状态机跟踪运行极大/极小，
   值低于 ``max − grad`` 判峰、高于 ``min + grad`` 判谷。grad 即"梯度"，
   决定峰谷切换需要的最小起伏。
2. **动态梯度**：按块（默认 1.25 s，按 fs 折算采样数）因果更新：
   ``grad = grad_factor × 上一块标准差``，下限 ``grad_min``。
   规格原文 th = dc_mean × CV% × 3（GCY）在 mmHg 零均值 AC 域归约为
   1.0×块标准差（推导与仿真实测见 docs/pipeline/peaks.md；CV>50 噪声
   门因零区分度被三层替代：梯度下限 + 相对 prominence + 邻搏一致性）。
3. **有效性过滤**（两阶段）：
   - 阶段 1（顺序、因果）：峰距——与上一通过项 <300 ms 剔除（防双峰/
     重搏波）；prominence——prom = 峰 − 前谷，< 0.25 × 近期通过项中位
     （窗口不足 prom_arm_n 时门控未启用）。
   - 阶段 2（中心邻窗）：邻搏一致性（Lim 2015）——与前后各 ≤lim_nbr 个
     通过项的幅度中位偏差 >50% 判离群。中心窗使包络爬升/回落段的单调
     趋势自然豁免（趋势中值与候选接近），只抓突变尖峰与畸变搏。
   峰距上限 1.5 s 仅作 HR 窗参考，不硬剔除（防漏搏连锁误删，中位数抗
   离群——偏离规格的工程决策，见文档）。
"""

from collections import deque
from dataclasses import dataclass

import numpy as np

from .params import AlgoParams


@dataclass(frozen=True)
class Candidate:
    """峰候选：检测器输出 + 有效性判决。"""

    idx: int          # 输入数组下标
    t_s: float        # 时间 [s]（来自外部时间轴）
    val: float        # 峰高度（AC 值）[mmHg]
    prom: float       # 相对前谷的起伏 [mmHg]
    accepted: bool
    reason: str = ""  # 剔除原因；accepted 时为空串


def _gradient_track(ac: np.ndarray, params: AlgoParams) -> np.ndarray:
    """因果分块梯度：块 k 使用块 k−1 的统计；首块用 grad_min。"""
    n = ac.size
    block = max(int(round(params.grad_block_s * params.fs)), 1)
    g = np.full(n, params.grad_min, dtype=float)
    for k in range(1, (n + block - 1) // block):
        prev = ac[(k - 1) * block: k * block]
        if prev.size < 2:
            continue
        gg = params.grad_factor * float(np.std(prev))
        g[k * block: min(n, (k + 1) * block)] = max(gg, params.grad_min)
    return g


def _hysteresis(ac: np.ndarray, g: np.ndarray):
    """双门限滞回检测 → [(峰 idx, 峰值, 相对前谷起伏)]。"""
    if ac.size == 0:
        return []
    raw = []
    cur_max = ac[0]
    max_idx = 0
    cur_min = ac[0]
    prev_trough = ac[0]
    seeking_peak = True
    for i in range(1, ac.size):
        v = ac[i]
        if seeking_peak:
            if v > cur_max:
                cur_max = v
                max_idx = i
            elif v < cur_max - g[i]:
                raw.append((max_idx, float(cur_max), float(cur_max - prev_trough)))
                seeking_peak = False
                cur_min = v
        else:
            if v < cur_min:
                cur_min = v
            elif v > cur_min + g[i]:
                prev_trough = cur_min
                seeking_peak = True
                cur_max = v
                max_idx = i
    return raw


def _apply_validity(raw, t_s: np.ndarray, params: AlgoParams):
    """两阶段有效性过滤：峰距/prominence（顺序）→ 邻搏一致性（中心窗）。

    中心窗需前后各 ≤lim_nbr 个通过项的幅度中位（至多引入 ~lim_nbr 搏
    延迟，内存有界，固件侧用小型环形缓冲即可）。
    """
    n = len(raw)
    out: list = [None] * n
    staged: list[int] = []
    prom_win: deque = deque(maxlen=params.prom_ref_n)
    last_ok_idx = None

    for j, (idx, val, prom) in enumerate(raw):
        reason = ""
        if last_ok_idx is not None:
            dt = (idx - last_ok_idx) / params.fs
            if dt < params.rr_min_s:
                reason = "峰距过近"
        if not reason and len(prom_win) >= params.prom_arm_n:
            ref = float(np.median(prom_win))
            if prom < params.prom_ratio * ref:
                reason = "prominence 不足"
        if reason:
            out[j] = Candidate(
                idx=int(idx), t_s=float(t_s[idx]), val=float(val),
                prom=float(prom), accepted=False, reason=reason,
            )
        else:
            staged.append(j)
            last_ok_idx = idx
            prom_win.append(prom)

    for k, j in enumerate(staged):
        idx, val, prom = raw[j]
        lo = max(0, k - params.lim_nbr)
        hi = min(len(staged), k + params.lim_nbr + 1)
        neigh = [raw[staged[m]][2] for m in range(lo, hi) if m != k]
        reason = ""
        if len(neigh) >= params.lim_nbr_min:
            ref = float(np.median(neigh))
            if ref > 0 and abs(prom - ref) / ref > params.lim_dev:
                reason = "邻搏幅度离群"
        out[j] = Candidate(
            idx=int(idx), t_s=float(t_s[idx]), val=float(val),
            prom=float(prom), accepted=not reason, reason=reason,
        )
    return [o for o in out if o is not None]


def detect(ac: np.ndarray, t_s: np.ndarray, params: AlgoParams | None = None):
    """AC 信号 → 峰候选列表（含被剔除项及原因）。"""
    params = params or AlgoParams()
    ac = np.asarray(ac, dtype=float)
    t_s = np.asarray(t_s, dtype=float)
    g = _gradient_track(ac, params)
    raw = _hysteresis(ac, g)
    return _apply_validity(raw, t_s, params)


def accepted_peaks(cands) -> list[Candidate]:
    """过滤后的有效峰（便捷入口）。"""
    return [c for c in cands if c.accepted]
