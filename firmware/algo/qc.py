"""质控移植：包络点数 / 残差 CV / 离群峰占比 / 判定理由合并。"""

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


class QCReport(object):
    def __init__(self, ok, reasons, outlier_ratio=None, env_cv=None):
        self.ok = bool(ok)
        self.reasons = tuple(reasons)
        self.outlier_ratio = outlier_ratio
        self.env_cv = env_cv


def _std(values):
    n = len(values)
    if n == 0:
        return 0.0
    mean = _q(sum(values) / n)
    acc = 0.0
    for value in values:
        d = _q(value - mean)
        acc = _q(acc + _q(d * d))
    return _q(math.sqrt(acc / n))


def check(env, candidates, decision, params=None):
    if params is None:
        params = AlgoParams()
    reasons = list(decision.reasons)

    if env.n_points < params.env_min_points:
        reasons.append(
            "包络点数不足（%d < %d）" % (env.n_points, params.env_min_points)
        )

    env_cv = None
    if env.n_points >= 2:
        mean_s = _q(sum(env.amp_s) / env.n_points)
        if mean_s > 0.0:
            diff = []
            for i in range(env.n_points):
                diff.append(_q(env.amp[i] - env.amp_s[i]))
            env_cv = _q(_std(diff) / mean_s)
            if env_cv > params.env_cv_max:
                reasons.append(
                    "包络 CV 超限（%.2f > %s）"
                    % (env_cv, params.env_cv_max)
                )

    outlier_ratio = None
    staged = []
    for candidate in candidates:
        if candidate.accepted or candidate.reason == "邻搏幅度离群":
            staged.append(candidate)
    if staged:
        n_lim = 0
        for candidate in staged:
            if not candidate.accepted:
                n_lim += 1
        outlier_ratio = _q(n_lim / len(staged))
        if outlier_ratio > params.outlier_ratio_max:
            reasons.append(
                "离群峰占比超限（%d%%）" % int(round(outlier_ratio * 100.0))
            )

    return QCReport(
        ok=not reasons,
        reasons=tuple(reasons),
        outlier_ratio=outlier_ratio,
        env_cv=env_cv,
    )
