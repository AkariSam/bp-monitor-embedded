"""形状锚定工具：求解 B+μ 非对称高斯顺应性族的三个参数，使包络对齐信号处理端锚点。

锚点（consensus，与 bp 管线常数对齐）：
    r_s = A(SBP)/Amax = 0.50      （bp.params.ratio_sbp）
    r_d = A(DBP)/Amax = 0.70      （bp.params.ratio_dbp）
    k   = (Ppeak−DBP)/PP = 0.41   （Dhamotharan 2023, N=122）

模型的三个无量纲参数（均按脉压 PP 缩放，跨血压自相似）：
    sigma_n_r = σₙ/PP   低谷侧（压闭）高斯宽度
    sigma_p_r = σₚ/PP   高峰侧（开放）高斯宽度
    mu_r      = μ/PP    顺应性峰相对 Pt=0 的偏移（负 = 轻度压闭侧）

用法：``.venv/Scripts/python scripts/fit_shape.py``（不导入 bp/sim，可独立运行）。
输出：求解结果 + 连续性/自相似性自检报告。

【重要】改锚点或改求解结果后须同步两处：
    - ``sim/model.py`` 的 ``PhysioParams`` 默认值
    - ``docs/sim/model.md`` §5.1（参数锚定表）

注意：锚点方程在该族中有多组根（当前 3 组，见 model.md §5.1 可辨识性说明）。
默认值采用 σₙ≪σₚ 且 μ<0 的生理方向分支（解空间扫描中 σₙ 最小的一支）。
"""

from __future__ import annotations

import sys

import numpy as np
from scipy.optimize import fsolve
from scipy.special import erf

V_MAX = 50.0      # 血容积幅值 [a.u.]
K_CUFF = 20.0     # 气路转换系数 [a.u./mmHg]
RT = np.sqrt(np.pi / 2.0)

S_REF, D_REF = 120.0, 80.0
TARGET_RS, TARGET_RD, TARGET_K = 0.50, 0.70, 0.41


def volume(pt, sn: float, sp: float, mu: float,
           v_max: float = V_MAX) -> np.ndarray:
    """非对称高斯顺应性 C(Pt) 的积分（erf 解析式，C¹ 光滑）。"""
    u = np.atleast_1d(np.asarray(pt, dtype=float)) - mu
    c0 = v_max / (RT * (sn + sp))
    out = np.empty_like(u)
    neg = u < 0
    out[neg] = c0 * RT * sn * (1.0 + erf(u[neg] / (np.sqrt(2.0) * sn)))
    out[~neg] = c0 * RT * (sn + sp * erf(u[~neg] / (np.sqrt(2.0) * sp)))
    return out


def compliance(pt, sn: float, sp: float, mu: float,
               v_max: float = V_MAX) -> np.ndarray:
    """顺应性 C(Pt) = C₀·exp(−(Pt−μ)²/(2σ(Pt)²))，分片点取在峰位 μ。"""
    u = np.asarray(pt, dtype=float) - mu
    sig = np.where(u < 0, sn, sp)
    c0 = v_max / (RT * (sn + sp))
    return c0 * np.exp(-(u * u) / (2.0 * sig * sig))


def envelope(pc, s: float, d: float, sn: float, sp: float, mu: float) -> np.ndarray:
    """包络 A(Pc) = [V(S−Pc) − V(D−Pc)]/k_cuff [mmHg]。"""
    return (volume(s - pc, sn, sp, mu) - volume(d - pc, sn, sp, mu)) / K_CUFF


def metrics(sn: float, sp: float, mu: float, s: float = S_REF, d: float = D_REF,
            pad: float = 80.0, n: int = 32001):
    """返回 (r_s, r_d, k, Amax, Ppeak)；峰值位置用抛物线细化。"""
    pc = np.linspace(d - pad, s + pad, n)
    a = envelope(pc, s, d, sn, sp, mu)
    i = int(np.argmax(a))
    step = pc[1] - pc[0]
    ppeak = float(pc[i])
    if 0 < i < n - 1:
        y0, y1, y2 = a[i - 1], a[i], a[i + 1]
        denom = y0 - 2.0 * y1 + y2
        if abs(denom) > 1e-30:
            delta = 0.5 * (y0 - y2) / denom
            ppeak = float(pc[i] + delta * step)
    amax = float(np.max(a))
    if not np.isfinite(amax) or amax <= 1e-12:
        nan = float("nan")
        return nan, nan, nan, amax, nan
    amp_s = float(envelope(np.array([s]), s, d, sn, sp, mu)[0])
    amp_d = float(envelope(np.array([d]), s, d, sn, sp, mu)[0])
    k = (ppeak - d) / (s - d)
    return amp_s / amax, amp_d / amax, k, amax, ppeak


def _residual(x):
    if not np.all(np.isfinite(x)) or abs(x[0]) > 10 or abs(x[1]) > 10 \
            or abs(x[2]) > 300:
        return [1e3, 1e3, 1e3]
    sn, sp, mu = float(np.exp(x[0])), float(np.exp(x[1])), float(x[2])
    rs, rd, k, amax, _ = metrics(sn, sp, mu)
    if not np.isfinite(amax) or amax < 1e-9:
        return [1e3, 1e3, 1e3]
    return [rs - TARGET_RS, rd - TARGET_RD, k - TARGET_K]


def solve_anchor():
    """多起点求解；返回去重后的解列表 [(sn, sp, mu), ...]。"""
    guesses = [(0.2, 0.9, -9.0), (0.3, 1.1, -12.0), (0.15, 0.5, -5.0),
               (0.4, 0.6, -15.0), (0.1, 1.3, -18.0), (0.6, 0.8, -25.0),
               (0.7, 0.05, 20.0)]
    sols: list[tuple[float, float, float]] = []
    print(f"[anchor] targets: r_s={TARGET_RS:.4f}  r_d={TARGET_RD:.4f}  "
          f"k={TARGET_K:.4f} @ ({S_REF:.0f}, {D_REF:.0f})")
    for gi, (sn_r, sp_r, mu_r) in enumerate(guesses):
        x0 = [np.log(sn_r * (S_REF - D_REF)), np.log(sp_r * (S_REF - D_REF)), mu_r]
        x, _, ier, _ = fsolve(_residual, x0, full_output=True)
        res = float(np.max(np.abs(_residual(x))))
        sn, sp, mu = float(np.exp(x[0])), float(np.exp(x[1])), float(x[2])
        print(f"[guess {gi}] ier={ier} maxres={res:.3e} -> "
              f"sn={sn:.4f} sp={sp:.4f} mu={mu:.4f}")
        if ier == 1 and res < 1e-7:
            dup = any(abs(sn - a) < 1e-3 and abs(sp - b) < 1e-3
                      and abs(mu - c) < 1e-3 for a, b, c in sols)
            if not dup:
                sols.append((sn, sp, mu))
    return sols


def _report_solution(sn: float, sp: float, mu: float) -> None:
    pp = S_REF - D_REF
    rs, rd, k, amax, ppeak = metrics(sn, sp, mu)
    print(f"  sigma_n = {sn:.8f} mmHg  (sigma_n/PP = {sn / pp:.8f})")
    print(f"  sigma_p = {sp:.8f} mmHg  (sigma_p/PP = {sp / pp:.8f})")
    print(f"  mu      = {mu:.8f} mmHg  (mu/PP      = {mu / pp:.8f})")
    print(f"  @120/80: r_s={rs:.6f} r_d={rd:.6f} k={k:.6f} "
          f"Amax={amax:.4f} mmHg Ppeak={ppeak:.4f}")

    # 连续性：C(μ±) 与 V 差分估计的顺应性
    eps = 1e-6
    c_left = float(compliance(np.array([mu - eps]), sn, sp, mu)[0])
    c_right = float(compliance(np.array([mu + eps]), sn, sp, mu)[0])
    c0_analytic = V_MAX / (RT * (sn + sp))
    c_num_left = float((volume(np.array([mu - eps]), sn, sp, mu)[0]
                        - volume(np.array([mu]), sn, sp, mu)[0]) / (-eps))
    c_num_right = float((volume(np.array([mu + eps]), sn, sp, mu)[0]
                         - volume(np.array([mu]), sn, sp, mu)[0]) / eps)
    print(f"  continuity: C(mu-)={c_left:.6f} C(mu+)={c_right:.6f} "
          f"C0={c0_analytic:.6f}")
    print(f"              dV/du(mu-)={c_num_left:.6f} dV/du(mu+)={c_num_right:.6f}")

    # 反例：分片点钉死在 Pt=0（错误设计）时的跳变（峰值仍在 μ）
    def c_fixed_split0(pt):
        u = np.asarray(pt, dtype=float) - mu
        sig = np.where(np.asarray(pt, dtype=float) < 0, sn, sp)
        return c0_analytic * np.exp(-(u * u) / (2.0 * sig * sig))
    c_at0_left = float(c_fixed_split0(np.array([-eps]))[0])
    c_at0_right = float(c_fixed_split0(np.array([eps]))[0])
    ratio = c_at0_left / c_at0_right if c_at0_right else float("inf")
    print(f"  counterfactual split@0: C(0-)={c_at0_left:.6f} "
          f"C(0+)={c_at0_right:.6f} ratio={ratio:.3f}")

    # 自相似性：参数按 PP 缩放
    print("  self-similar:")
    for s2, d2 in [(160.0, 100.0), (90.0, 60.0), (150.0, 70.0)]:
        f = (s2 - d2) / pp
        rs2, rd2, k2, a2, pk2 = metrics(sn * f, sp * f, mu * f, s2, d2)
        print(f"    ({s2:.0f}/{d2:.0f}): r_s={rs2:.6f} r_d={rd2:.6f} "
              f"k={k2:.6f} Amax={a2:.4f} Ppeak={pk2:.2f}")


def main() -> int:
    sols = solve_anchor()
    print(f"[unique solutions] {len(sols)}")
    if not sols:
        print("[FAIL] 无解：锚点在该族不可达，需回退讨论")
        return 1
    for j, (sn, sp, mu) in enumerate(sols):
        print(f"[solution {j}]")
        _report_solution(sn, sp, mu)
    return 0


if __name__ == "__main__":
    sys.exit(main())
