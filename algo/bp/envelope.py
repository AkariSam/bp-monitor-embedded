"""包络构建：逐搏（袖带压, 振幅）→ 排序、中值 + 滑动平均平滑（§4.3）。

- 输入：逐搏记录 {袖带压 Pc, 振幅 A}（固件侧 int16 存储 {P×10, A×100}，
  PC 侧保持 float64）。
- 处理：按压力升序排序（放气方向统一）→ 3 点中值去毛刺 → 3 点滑动平均。
- 点数不足的判废在 qc 模块执行（本模块不抛错，只构造）。
"""

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter, uniform_filter1d

from .params import AlgoParams


@dataclass(frozen=True)
class Envelope:
    """包络数据（按袖带压升序）。"""

    pc: np.ndarray    # 逐搏袖带压 [mmHg]
    amp: np.ndarray   # 逐搏原始振幅 [mmHg]
    amp_s: np.ndarray  # 平滑后振幅 [mmHg]

    @property
    def n_points(self) -> int:
        return int(self.pc.size)


def smooth(amp: np.ndarray, median_w: int = 3, ma_w: int = 3) -> np.ndarray:
    """中值 + 滑动平均（边缘复制模式，与固件实现约定一致）。"""
    amp = np.asarray(amp, dtype=float)
    if amp.size == 0:
        return amp.copy()
    y = amp
    if median_w >= 2:
        y = median_filter(y, size=median_w, mode="nearest")
    if ma_w >= 2:
        y = uniform_filter1d(y, size=ma_w, mode="nearest")
    return y


def build(pc, amp, params: AlgoParams | None = None) -> Envelope:
    """逐搏对 → Envelope（排序 + 平滑）。"""
    params = params or AlgoParams()
    pc = np.asarray(pc, dtype=float)
    amp = np.asarray(amp, dtype=float)
    order = np.argsort(pc, kind="stable")
    pc_sorted = pc[order]
    amp_sorted = amp[order]
    return Envelope(
        pc=pc_sorted,
        amp=amp_sorted,
        amp_s=smooth(amp_sorted, params.env_median, params.env_ma),
    )


def from_peaks(dc: np.ndarray, ac: np.ndarray, peaks):
    """有效峰 + 对齐信号 → (pc, amp) 逐搏对。

    - 袖带压 = 峰时刻的对齐 DC 值（逐点取值，避免窗口均值在边界被拉偏）；
    - 振幅 = 搏动中点窗内 AC 峰峰值（ptp），首末搏向两侧取半个 RR 的
      对称窗（不多吃空白区，与可视化粗包络、σ 标定口径一致）。
    """
    idxs = [int(p.idx) for p in peaks]
    n = int(ac.size)
    m = len(idxs)
    if m == 0:
        return np.array([]), np.array([])
    mids = [(idxs[i] + idxs[i + 1]) // 2 for i in range(m - 1)]
    pc_list, amp_list = [], []
    for i, pk in enumerate(idxs):
        if m == 1:
            lo, hi = 0, n
        else:
            lo = max(0, pk - (idxs[1] - pk) // 2) if i == 0 else mids[i - 1]
            hi = min(n, pk + (pk - idxs[i - 1]) // 2) if i == m - 1 else mids[i]
        if hi <= lo:
            continue
        pc_list.append(float(dc[pk]))
        amp_list.append(float(np.ptp(ac[lo:hi])))
    return np.array(pc_list, dtype=float), np.array(amp_list, dtype=float)
