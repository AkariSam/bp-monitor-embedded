"""标定预演：BP 网格扫描 + 线性校准拟合评估（可行性实验，不改动管线）。

在仿真 BP 网格（多组 SBP×DBP 组合）上跑完整管线，比较校准模型的残差：

- ① 原始（未校准）      err = est − truth
- ② 每输出 1 维线性      truth ≈ a·est + b
- ③ 每输出 2 维线性      truth ≈ a·est_out + b·est_other + c（脉搏压交叉项）
- ④ 合并单拟合（SBP+DBP 共用一套 a,b）—— 反例对照
- ⑤ 2×4×2 MLP（2 输入 → 4 tanh → 2 输出）—— 非线性对照，多初始化

真值仅在**拟合与评估**中使用；管线（bp/）不接触真值。数据全程在内存
（不落 CSV）。注意：仿真拟合出的系数**不代表真机标定值**——真机系数必须
用真人参考数据（T1/T2）重估，本脚本只验证标定方法与残差量级。

用法（algo/ 下，先激活 .venv）：
  python scripts/calibrate.py
  python scripts/calibrate.py --sbp 90 180 10 --dbp 50 110 10
"""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from scipy.optimize import minimize

from bp.csvio import Recording
from bp.params import AlgoParams
from bp.pipeline import run
from sim import artifacts, model, scenarios
from sim.generate import RNG_STREAMS, sample_grid

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "out" / "eval"


def make_recording(physio, art, seed: int, fs_out: float) -> Recording:
    """与 sim.generate 同构的内存版生成（自定义 PhysioParams）。"""
    rngs = dict(zip(RNG_STREAMS,
                    np.random.SeedSequence(seed).spawn(len(RNG_STREAMS))))
    sig = model.synthesize(physio, rng=np.random.default_rng(rngs["physio"]))
    p_dense = artifacts.apply_artifacts(
        sig.p_clean, sig.t, art,
        rng_noise=np.random.default_rng(rngs["noise"]),
        rng_breath=np.random.default_rng(rngs["breath"]),
    )
    t_ms, idx = sample_grid(sig.t, model.FS_SIM, fs_out)
    return Recording(
        t_ms=t_ms,
        p_raw=artifacts.quantize(p_dense[idx], model.LSB),
        phase=sig.phase[idx],
    )


def resid_stats(y_true, y_pred) -> dict:
    r = np.asarray(y_true) - np.asarray(y_pred)
    return {
        "mean": float(np.mean(r)),
        "std": float(np.std(r)),
        "rmse": float(np.sqrt(np.mean(r ** 2))),
        "max_abs": float(np.max(np.abs(r))),
    }


def fit1(x, y):
    a, b = np.polyfit(np.asarray(x), np.asarray(y), 1)
    return (float(a), float(b))


def fit2(x1, x2, y):
    X = np.column_stack([x1, x2, np.ones_like(x1)])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return tuple(float(v) for v in coef)


def mlp_calibration_report(x_tr, y_tr, x_te, y_te, n_init=5, lam=1e-4, seed=0):
    """2×4×2 MLP 校准（tanh 隐层、线性输出、输入输出标准化、L2 正则）。

    返回每次随机初始化的训练/测试残差统计（按分布报告，不事后挑最优）。
    """
    mx = x_tr.mean(axis=0)
    sx = x_tr.std(axis=0) + 1e-12
    my = y_tr.mean(axis=0)
    sy = y_tr.std(axis=0) + 1e-12
    Xt = (x_tr - mx) / sx
    Yt = (y_tr - my) / sy

    def unpack(theta):
        return (theta[:8].reshape(2, 4), theta[8:12],
                theta[12:20].reshape(2, 4), theta[20:22])

    def fg(theta, X, Y):
        W1, b1, W2, b2 = unpack(theta)
        h = np.tanh(X @ W1 + b1)
        r = h @ W2.T + b2 - Y
        loss = (0.5 * float(np.mean(np.sum(r ** 2, axis=1)))
                + 0.5 * lam * float(np.sum(W1 ** 2) + np.sum(W2 ** 2)))
        dy = r / X.shape[0]
        gW2 = dy.T @ h + lam * W2
        gb2 = dy.sum(axis=0)
        dz = (dy @ W2) * (1.0 - h ** 2)
        gW1 = X.T @ dz + lam * W1
        gb1 = dz.sum(axis=0)
        return loss, np.concatenate([gW1.ravel(), gb1, gW2.ravel(), gb2])

    rng = np.random.default_rng(seed)
    stats = []
    for k in range(n_init):
        theta0 = 0.5 * rng.standard_normal(22)
        res = minimize(lambda t: fg(t, Xt, Yt), theta0, jac=True,
                       method="L-BFGS-B", options={"maxiter": 1000})
        W1, b1, W2, b2 = unpack(res.x)

        def predict(xx):
            Xs = (np.asarray(xx) - mx) / sx
            hnew = np.tanh(Xs @ W1 + b1)
            return (hnew @ W2.T + b2) * sy + my

        p_tr = predict(x_tr)
        p_te = predict(x_te)
        stats.append({
            "init": k,
            "success": bool(res.success),
            "train_sbp": resid_stats(y_tr[:, 0], p_tr[:, 0]),
            "train_dbp": resid_stats(y_tr[:, 1], p_tr[:, 1]),
            "test_sbp": resid_stats(y_te[:, 0], p_te[:, 0]),
            "test_dbp": resid_stats(y_te[:, 1], p_te[:, 1]),
        })
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="BP 网格扫描与线性校准预演")
    ap.add_argument("--sbp", type=int, nargs=3, default=[90, 180, 10],
                    metavar=("LO", "HI", "STEP"))
    ap.add_argument("--dbp", type=int, nargs=3, default=[50, 110, 10],
                    metavar=("LO", "HI", "STEP"))
    ap.add_argument("--pp-min", type=int, default=25, help="最小脉压 [mmHg]")
    ap.add_argument("--hr", type=float, default=70.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--fs", type=float, default=80.0)
    ap.add_argument("--split", type=float, default=0.7, help="训练集比例")
    ap.add_argument("--mlp-init", type=int, default=5,
                    help="2×4×2 MLP 随机初始化次数")
    ap.add_argument("--json", type=Path, default=EVAL_DIR / "calibration.json")
    args = ap.parse_args(argv)

    art = scenarios.get_scenario("normal")[1]
    params = replace(AlgoParams(), fs=args.fs)
    sbps = range(args.sbp[0], args.sbp[1] + 1, args.sbp[2])
    dbps = range(args.dbp[0], args.dbp[1] + 1, args.dbp[2])
    grid = [(float(s), float(d)) for s in sbps for d in dbps
            if s - d >= args.pp_min]
    print(f"网格 {len(grid)} 组合：SBP {args.sbp[0]}..{args.sbp[1]}/{args.sbp[2]}"
          f" × DBP {args.dbp[0]}..{args.dbp[1]}/{args.dbp[2]}，PP≥{args.pp_min}，"
          f"HR={args.hr:.0f}，seed={args.seed}")

    rows, rejected = [], []
    for s, d in grid:
        physio = replace(model.PhysioParams(), sbp=s, dbp=d, hr=args.hr)
        rec = make_recording(physio, art, args.seed, args.fs)
        res = run(rec, params)
        if not res.accepted or res.decision.sbp is None:
            rejected.append((s, d, list(res.qc.reasons)))
            continue
        rows.append({
            "sbp_true": s, "dbp_true": d,
            "sbp_est": res.decision.sbp, "dbp_est": res.decision.dbp,
            "map_est": res.decision.map, "pp_true": s - d,
        })
    print(f"接受 {len(rows)} / 拒绝 {len(rejected)}")
    for s, d, reasons in rejected:
        print(f"  reject {s:.0f}/{d:.0f}: {'; '.join(reasons)}")
    if len(rows) < 8:
        print("有效点不足，终止")
        return 1

    st = np.array([r["sbp_true"] for r in rows])
    dt = np.array([r["dbp_true"] for r in rows])
    se = np.array([r["sbp_est"] for r in rows])
    de = np.array([r["dbp_est"] for r in rows])

    n = len(rows)
    perm = np.random.default_rng(0).permutation(n)
    n_tr = int(round(args.split * n))
    tr, te = perm[:n_tr], perm[n_tr:]

    raw_s = resid_stats(st, se)
    raw_d = resid_stats(dt, de)

    a1s, b1s = fit1(se[tr], st[tr])
    a1d, b1d = fit1(de[tr], dt[tr])
    a2s, b2s, c2s = fit2(se[tr], de[tr], st[tr])
    a2d, b2d, c2d = fit2(de[tr], se[tr], dt[tr])
    ap_, bp_ = fit1(np.concatenate([se[tr], de[tr]]),
                    np.concatenate([st[tr], dt[tr]]))

    mlp_stats = mlp_calibration_report(
        np.column_stack([se[tr], de[tr]]), np.column_stack([st[tr], dt[tr]]),
        np.column_stack([se[te], de[te]]), np.column_stack([st[te], dt[te]]),
        n_init=args.mlp_init)
    m_test = np.array([[s["test_sbp"]["rmse"], s["test_dbp"]["rmse"]]
                       for s in mlp_stats])
    m_max = np.array([[s["test_sbp"]["max_abs"], s["test_dbp"]["max_abs"]]
                      for s in mlp_stats])
    m_train = np.array([[s["train_sbp"]["rmse"], s["train_dbp"]["rmse"]]
                        for s in mlp_stats])

    def one_dim(coef, x):
        a, b = coef
        return a * x + b

    results = {
        "grid": {"n_grid": len(grid), "n_accepted": len(rows),
                 "n_rejected": len(rejected), "n_train": n_tr, "n_test": n - n_tr},
        "raw": {"sbp": raw_s, "dbp": raw_d},
        "sbp_1d": {"coef": [a1s, b1s],
                   "train": resid_stats(st[tr], one_dim((a1s, b1s), se[tr])),
                   "test": resid_stats(st[te], one_dim((a1s, b1s), se[te]))},
        "dbp_1d": {"coef": [a1d, b1d],
                   "train": resid_stats(dt[tr], one_dim((a1d, b1d), de[tr])),
                   "test": resid_stats(dt[te], one_dim((a1d, b1d), de[te]))},
        "sbp_2d": {"coef": [a2s, b2s, c2s],
                   "train": resid_stats(st[tr], a2s * se[tr] + b2s * de[tr] + c2s),
                   "test": resid_stats(st[te], a2s * se[te] + b2s * de[te] + c2s)},
        "dbp_2d": {"coef": [a2d, b2d, c2d],
                   "train": resid_stats(dt[tr], a2d * de[tr] + b2d * se[tr] + c2d),
                   "test": resid_stats(dt[te], a2d * de[te] + b2d * se[te] + c2d)},
        "pooled_1d": {"coef": [ap_, bp_],
                      "test_sbp": resid_stats(st[te], one_dim((ap_, bp_), se[te])),
                      "test_dbp": resid_stats(dt[te], one_dim((ap_, bp_), de[te]))},
        "mlp_2x4x2": {
            "n_init": args.mlp_init,
            "stats": mlp_stats,
            "summary": {
                "test_sbp_rmse_mean": float(m_test[:, 0].mean()),
                "test_sbp_rmse_std": float(m_test[:, 0].std()),
                "test_dbp_rmse_mean": float(m_test[:, 1].mean()),
                "test_dbp_rmse_std": float(m_test[:, 1].std()),
            },
        },
        "points": rows,
    }

    def fmt(d):
        return f"rmse {d['rmse']:.2f}  max|e| {d['max_abs']:.2f}  (mean {d['mean']:+.2f})"

    print("\n== 未校准（全体网格点）==")
    print(f"  SBP: {fmt(raw_s)}")
    print(f"  DBP: {fmt(raw_d)}")
    print(f"\n== 每输出 1 维线性校准  truth = a·est + b（训练 {n_tr} / 测试 {n - n_tr}）==")
    print(f"  SBP: a={a1s:.4f} b={b1s:+.3f} | train {fmt(results['sbp_1d']['train'])}"
          f" | test {fmt(results['sbp_1d']['test'])}")
    print(f"  DBP: a={a1d:.4f} b={b1d:+.3f} | train {fmt(results['dbp_1d']['train'])}"
          f" | test {fmt(results['dbp_1d']['test'])}")
    print(f"\n== 每输出 2 维线性校准  truth = a·est_out + b·est_other + c（含脉搏压交叉项）==")
    print(f"  SBP: a={a2s:.4f} b={b2s:+.4f} c={c2s:+.3f} | test {fmt(results['sbp_2d']['test'])}")
    print(f"  DBP: a={a2d:.4f} b={b2d:+.4f} c={c2d:+.3f} | test {fmt(results['dbp_2d']['test'])}")
    print(f"\n== 合并单拟合（SBP+DBP 共用, 反例）: a={ap_:.4f} b={bp_:+.3f} ==")
    print(f"  test SBP {fmt(results['pooled_1d']['test_sbp'])}")
    print(f"  test DBP {fmt(results['pooled_1d']['test_dbp'])}")
    print(f"\n== 2×4×2 MLP 校准（2 输入 → 4 tanh → 2 输出，{args.mlp_init} 次随机初始化）==")
    print(f"  test rmse: SBP {m_test[:, 0].mean():.2f} ± {m_test[:, 0].std():.2f}"
          f"（范围 {m_test[:, 0].min():.2f}~{m_test[:, 0].max():.2f}）"
          f" | DBP {m_test[:, 1].mean():.2f} ± {m_test[:, 1].std():.2f}"
          f"（范围 {m_test[:, 1].min():.2f}~{m_test[:, 1].max():.2f}）")
    print(f"  test max|e|（初始化中最优）: SBP {m_max[:, 0].min():.2f}"
          f" / DBP {m_max[:, 1].min():.2f}"
          f" | train rmse 均值: {m_train[:, 0].mean():.2f}/{m_train[:, 1].mean():.2f}")

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"\n[ok] 结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())