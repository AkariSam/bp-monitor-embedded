"""质控：判废理由汇总（§4.4）。

检查项（全部可识别、拒绝出数而非静默给值）：

- 包络点数 <8 判废；
- 包络残差 CV（粗糙度 = std(原始 − 平滑)/mean(平滑)）超限判废；
- Lim 离群峰占比（离群 / 进入邻搏门控的通过项）超限判废；
- 判定阶段的交叉缺失理由（SBP/DBP 交叉未找到）直接并入。

运动伪影专项门控未实现（仿真未建模运动，见 docs/sim/artifacts.md §5
deferred 清单）；真实数据到位后按需增补。
"""

from dataclasses import dataclass

import numpy as np

from .decision import Decision
from .envelope import Envelope
from .params import AlgoParams


@dataclass(frozen=True)
class QCReport:
    """质控报告。"""

    ok: bool
    reasons: tuple[str, ...]
    outlier_ratio: float | None = None  # Lim 离群占比
    env_cv: float | None = None         # 包络残差 CV（粗糙度）


def check(env: Envelope, candidates, decision: Decision,
          params: AlgoParams | None = None) -> QCReport:
    """包络 + 峰候选 + 判定 → QCReport。"""
    params = params or AlgoParams()
    reasons = list(decision.reasons)

    if env.n_points < params.env_min_points:
        reasons.append(f"包络点数不足（{env.n_points} < {params.env_min_points}）")

    env_cv: float | None = None
    if env.n_points >= 2:
        mean_s = float(np.mean(env.amp_s))
        if mean_s > 0:
            env_cv = float(np.std(env.amp - env.amp_s) / mean_s)
            if env_cv > params.env_cv_max:
                reasons.append(f"包络 CV 超限（{env_cv:.2f} > {params.env_cv_max}）")

    outlier_ratio: float | None = None
    staged = [c for c in candidates
              if c.accepted or c.reason == "邻搏幅度离群"]
    if staged:
        n_lim = sum(1 for c in staged if not c.accepted)
        outlier_ratio = n_lim / len(staged)
        if outlier_ratio > params.outlier_ratio_max:
            reasons.append(f"离群峰占比超限（{outlier_ratio:.0%}）")

    return QCReport(
        ok=not reasons, reasons=tuple(reasons),
        outlier_ratio=outlier_ratio, env_cv=env_cv,
    )
