"""质控（QCReport）的单元测试。"""

import numpy as np

from bp.decision import Decision
from bp.envelope import Envelope
from bp.params import AlgoParams
from bp.peaks import Candidate
from bp.qc import check


def _env(n=10, amp=None, amp_s=None):
    pc = np.arange(n, dtype=float) * 4 + 60
    if amp_s is None:
        amp_s = np.ones(n)
    if amp is None:
        amp = amp_s.copy()
    return Envelope(pc=pc, amp=np.asarray(amp, float), amp_s=np.asarray(amp_s, float))


def _cands(n_acc=10, n_lim=0):
    out = [Candidate(i, float(i), 1.0, 1.0, True, "") for i in range(n_acc)]
    out += [Candidate(100 + i, 100.0 + i, 1.0, 3.0, False, "邻搏幅度离群")
            for i in range(n_lim)]
    return out


def test_clean_pass():
    rep = check(_env(), _cands(), Decision())
    assert rep.ok
    assert rep.reasons == ()
    assert rep.outlier_ratio == 0.0
    assert rep.env_cv is not None and rep.env_cv < 1e-9


def test_short_envelope_rejected():
    rep = check(_env(n=5), _cands(), Decision())
    assert not rep.ok
    assert any("包络点数不足" in r for r in rep.reasons)


def test_rough_envelope_rejected():
    amp = 1.0 + 0.5 * ((-1.0) ** np.arange(10))
    rep = check(_env(amp=amp), _cands(), Decision())
    assert rep.env_cv is not None and rep.env_cv > AlgoParams().env_cv_max
    assert any("包络 CV 超限" in r for r in rep.reasons)


def test_outlier_ratio_rejected():
    rep = check(_env(), _cands(n_acc=6, n_lim=4), Decision())
    assert rep.outlier_ratio is not None and rep.outlier_ratio > 0.3
    assert any("离群峰占比超限" in r for r in rep.reasons)


def test_decision_reasons_merged():
    rep = check(_env(), _cands(), Decision(reasons=("SBP 交叉未找到",)))
    assert not rep.ok
    assert "SBP 交叉未找到" in rep.reasons