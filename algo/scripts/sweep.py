"""参数扫描实验工具（四模式）：algo / stress / ratio / sim。

红线（与实验计划一致）：只测量、不修改任何默认值；结论供人工决策。
真值仅在本脚本层读取，``bp/`` 与 ``sim/`` 均不接触真值。
结果 JSON → ``out/eval/``，图 → ``out/figs/``（均可再生，不进 git）。

用法（在 algo/ 目录下，先激活 .venv）：
  python scripts/sweep.py --mode algo     # 实验一：算法参数敏感性（OAT）
  python scripts/sweep.py --mode stress   # 实验二：伪影压力扫描
  python scripts/sweep.py --mode ratio    # 实验三：比率换算率验证（包络复用）
  python scripts/sweep.py --mode sim      # 实验四：仿真器生理与机动参数
"""

import argparse
import contextlib
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from bp.csvio import read_csv
from bp.decision import decide
from bp.params import AlgoParams
from bp.peaks import accepted_peaks
from bp.pipeline import run
from sim import model, scenarios
from sim.artifacts import ArtifactParams
from sim.generate import generate
from sim.model import PhysioParams

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "out" / "eval"
FIG_DIR = ROOT / "out" / "figs"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

ALL_SCENARIOS = sorted(scenarios.SCENARIOS)
SEEDS3 = (1, 2, 3)
SEEDS5 = (1, 2, 3, 4, 5)


def _r(x, n=3):
    """numpy 标量 → 原生 float（JSON 序列化安全）。"""
    return None if x is None else round(float(x), n)


def _metrics(res, truth) -> dict:
    """一次管线运行的可记录指标。"""
    d = res.decision
    row = {
        "accepted": bool(res.accepted),
        "n_points": res.envelope.n_points,
        "n_candidates": res.n_candidates,
        "n_accepted": res.n_accepted,
        "arrhythmia": bool(d.arrhythmia),
        "dia_flag": bool(d.dia_flag),
        "reasons": list(res.qc.reasons),
    }
    for k in ("sbp", "dbp", "hr"):
        v = getattr(d, k)
        row[f"err_{k}"] = None if v is None else float(v - truth[k])
    return row


def _gen_rec(name, seed, workdir, physio=None, art=None, lsb=None):
    """生成一条记录并读回（真值在本脚本层使用）。"""
    csv_path, truth_path = generate(name, seed, workdir, physio=physio,
                                    art=art, lsb=lsb)
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    return read_csv(csv_path), truth


def _save_fig(fig, path: Path) -> Path:
    """保存（Windows 查看器占用时回退 _alt 后缀）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.savefig(path, dpi=140)
    except PermissionError:
        alt = path.with_name(f"{path.stem}_alt{path.suffix}")
        fig.savefig(alt, dpi=140)
        print(f"[warn] {path.name} 被占用，改存 {alt.name}")
        path = alt
    plt.close(fig)
    return path


# ======================================================================
# 实验一：算法参数敏感性（OAT）
# ======================================================================

ALGO_GRID = {
    "grad_min": [0.25, 0.375, 0.5, 0.75, 1.0],
    "prom_ratio": [0.10, 0.17, 0.25, 0.35, 0.50],
    "env_median": [1, 3, 5],
    "env_ma": [1, 3, 5],
    "edge_margin_s": [0.25, 0.5, 1.0],
    "grad_factor": [0.5, 0.75, 1.0, 1.5, 2.0],
    "grad_block_s": [0.6, 1.25, 2.5],
    "prom_ref_n": [5, 10, 20],
    "prom_arm_n": [1, 3, 5],
    "lim_dev": [0.3, 0.5, 0.7],
}


def _delta_stats(baseline: dict, level: dict) -> dict:
    """相对基准：误差最大变化、接受翻转、MAE。"""
    max_d = {"sbp": 0.0, "dbp": 0.0, "hr": 0.0}
    false_reject, false_accept = [], []
    mae = {"sbp": [], "dbp": []}
    n_acc = 0
    for key, b in baseline.items():
        l = level[key]
        name, s = key
        tag = f"{name}_s{s:03d}"
        if b["accepted"] and not l["accepted"]:
            false_reject.append(tag)
        if (not b["accepted"]) and l["accepted"]:
            false_accept.append(tag)
        if b["accepted"] and l["accepted"]:
            n_acc += 1
            for k in ("sbp", "dbp", "hr"):
                eb, el = b[f"err_{k}"], l[f"err_{k}"]
                if eb is not None and el is not None:
                    max_d[k] = max(max_d[k], abs(el - eb))
            for k in ("sbp", "dbp"):
                if l[f"err_{k}"] is not None:
                    mae[k].append(abs(l[f"err_{k}"]))
    return {
        "max_delta_sbp": _r(max_d["sbp"]),
        "max_delta_dbp": _r(max_d["dbp"]),
        "max_delta_hr": _r(max_d["hr"]),
        "false_reject": false_reject,
        "false_accept": false_accept,
        "mae_sbp": _r(np.mean(mae["sbp"])) if mae["sbp"] else None,
        "mae_dbp": _r(np.mean(mae["dbp"])) if mae["dbp"] else None,
        "n_accepted": n_acc,
    }


def _verdict(max_sbp, max_dbp, max_hr, n_false_reject, n_false_accept) -> str:
    """钉死 / 观察 / 脆弱。"""
    m = max(max_sbp, max_dbp)
    if n_false_accept > 0 or m >= 2.0 or n_false_reject >= 3:
        return "脆弱"
    if m >= 0.5 or n_false_reject > 0 or max_hr >= 2.0:
        return "观察"
    return "钉死"


def run_algo(names=None, seeds=SEEDS3, grid=None, make_fig=True,
             workdir=None) -> dict:
    """实验一：一次只动一个算法参数，记录相对基准的输出变化。"""
    names = names or ALL_SCENARIOS
    grid = grid or ALGO_GRID
    base = AlgoParams()
    with contextlib.ExitStack() as stack:
        wd = (Path(workdir) if workdir
              else Path(stack.enter_context(
                  tempfile.TemporaryDirectory(prefix="sweep_algo_"))))
        recs = {(name, s): _gen_rec(name, s, wd)
                for name in names for s in seeds}
        baseline = {k: _metrics(run(rec, base), truth)
                    for k, (rec, truth) in recs.items()}

        results = {
            "baseline": {
                "mae_sbp": _r(np.mean([abs(m["err_sbp"]) for m in baseline.values()
                                       if m["accepted"] and m["err_sbp"] is not None])),
                "mae_dbp": _r(np.mean([abs(m["err_dbp"]) for m in baseline.values()
                                       if m["accepted"] and m["err_dbp"] is not None])),
                "n_accepted": sum(m["accepted"] for m in baseline.values()),
                "n_total": len(baseline),
            },
            "grid_names": names,
            "grid_seeds": list(seeds),
            "params": {},
        }
        for param, levels in grid.items():
            default = getattr(base, param)
            entry = {"default": default, "levels": {}}
            agg = {"sbp": 0.0, "dbp": 0.0, "hr": 0.0,
                   "reject": [], "accept": []}
            for lv in levels:
                lv = float(lv) if isinstance(lv, float) else int(lv)
                if lv == default:
                    stats = _delta_stats(baseline, baseline)
                else:
                    p2 = replace(base, **{param: lv})
                    lvl = {k: _metrics(run(rec, p2), truth)
                           for k, (rec, truth) in recs.items()}
                    stats = _delta_stats(baseline, lvl)
                entry["levels"][str(lv)] = stats
                agg["sbp"] = max(agg["sbp"], stats["max_delta_sbp"])
                agg["dbp"] = max(agg["dbp"], stats["max_delta_dbp"])
                agg["hr"] = max(agg["hr"], stats["max_delta_hr"])
                agg["reject"] += stats["false_reject"]
                agg["accept"] += stats["false_accept"]
            entry["max_delta_sbp"] = _r(agg["sbp"])
            entry["max_delta_dbp"] = _r(agg["dbp"])
            entry["max_delta_hr"] = _r(agg["hr"])
            entry["false_reject"] = agg["reject"]
            entry["false_accept"] = agg["accept"]
            entry["verdict"] = _verdict(agg["sbp"], agg["dbp"], agg["hr"],
                                        len(agg["reject"]), len(agg["accept"]))
            results["params"][param] = entry

        if make_fig:
            _plot_algo(results, FIG_DIR / "sweep_algo.png")
    return results


def _plot_algo(res: dict, path: Path) -> Path:
    params = list(res["params"])
    y = np.arange(len(params))
    ms = [res["params"][p]["max_delta_sbp"] or 0 for p in params]
    md = [res["params"][p]["max_delta_dbp"] or 0 for p in params]
    mh = [res["params"][p]["max_delta_hr"] or 0 for p in params]
    nrej = [len(res["params"][p]["false_reject"]) for p in params]
    nacc = [len(res["params"][p]["false_accept"]) for p in params]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6.5),
                                   constrained_layout=True, sharey=True)
    ax1.barh(y - 0.2, ms, height=0.4, color="#bb3333", label="SBP")
    ax1.barh(y + 0.2, md, height=0.4, color="#337733", label="DBP")
    ax1.axvline(0.5, color="#999999", ls=":", lw=0.9)
    ax1.axvline(2.0, color="#999999", ls="--", lw=0.9)
    ax1.set_yticks(y, [f"{p}" for p in params])
    ax1.invert_yaxis()
    ax1.set_xlabel("与基准的最大误差变化 [mmHg]")
    ax1.set_title("血压：参数扰动 ±（相对默认）的最大读数变化")
    ax1.legend(loc="lower right", fontsize=9)
    for yi, (a, b) in enumerate(zip(nrej, nacc)):
        if a or b:
            ax1.text(ax1.get_xlim()[1] * 0.98, yi, f"误拒{a}/误收{b}",
                     ha="right", va="center", fontsize=8, color="#aa5500")

    ax2.barh(y, mh, height=0.5, color="#2255aa")
    ax2.axvline(2.0, color="#999999", ls="--", lw=0.9)
    ax2.set_xlabel("与基准的最大误差变化 [bpm]")
    ax2.set_title("心率：参数扰动的最大读数变化")
    fig.suptitle("实验一：算法参数敏感性筛查（OAT，8 场景 × 3 种子）", fontsize=13)
    return _save_fig(fig, path)


# ======================================================================
# 实验二：伪影压力扫描
# ======================================================================

STRESS_SERIES = [
    ("noise_sigma", "art", [0.02, 0.05, 0.1, 0.2, 0.4]),
    ("breathing_amp", "art", [0.0, 0.5, 1.0, 2.0]),
    ("beat_jitter", "physio", [0.0, 0.1, 0.2, 0.3]),
    ("deflate_rate", "physio", [2.0, 3.0, 5.0]),
    ("mains_on", "art", [False, True]),
]

WORST_COMBO = {
    "art": {"noise_sigma": 0.2, "breathing_amp": 1.0},
    "physio": {"deflate_rate": 5.0, "beat_jitter": 0.2},
}


def _stress_cell(names, seeds, wd_cell, art_over=None, physio_over=None) -> dict:
    """一个压力档位：在**各场景自身参数**上叠加覆盖（不重置场景血压等）。"""
    rows = {}
    for name in names:
        base_physio, base_art = scenarios.get_scenario(name)
        physio = replace(base_physio, **physio_over) if physio_over else None
        art = replace(base_art, **art_over) if art_over else None
        for s in seeds:
            rec, truth = _gen_rec(name, s, wd_cell, physio=physio, art=art)
            rows[(name, s)] = _metrics(run(rec), truth)
    return rows


def _stress_stats(rows: dict) -> dict:
    steady = {k: m for k, m in rows.items() if k[0] != "truncated"}
    trunc = {k: m for k, m in rows.items() if k[0] == "truncated"}
    acc = [m for m in steady.values() if m["accepted"]]
    errs_sbp = [abs(m["err_sbp"]) for m in acc if m["err_sbp"] is not None]
    errs_dbp = [abs(m["err_dbp"]) for m in acc if m["err_dbp"] is not None]
    pts = [m["n_points"] for m in acc]
    return {
        "steady_accept_rate": _r(np.mean([m["accepted"] for m in steady.values()])),
        "truncated_reject_rate": _r(np.mean([not m["accepted"] for m in trunc.values()]))
        if trunc else None,
        "mae_sbp": _r(np.mean(errs_sbp)) if errs_sbp else None,
        "mae_dbp": _r(np.mean(errs_dbp)) if errs_dbp else None,
        "n_points_mean": _r(np.mean(pts), 1) if pts else None,
    }


def _is_default(var: str, side: str, lv) -> bool:
    src = ArtifactParams() if side == "art" else PhysioParams()
    return getattr(src, var) == lv


def _lv_label(lv, is_def: bool) -> str:
    s = ("开" if lv else "关") if isinstance(lv, bool) else f"{lv:g}"
    return f"{s}(默认)" if is_def else s


def run_stress(names=None, seeds=SEEDS5, make_fig=True, workdir=None) -> dict:
    """实验二：一次只加一个压力源，找「断点」。"""
    names = names or ALL_SCENARIOS
    with contextlib.ExitStack() as stack:
        wd = (Path(workdir) if workdir
              else Path(stack.enter_context(
                  tempfile.TemporaryDirectory(prefix="sweep_stress_"))))
        base_rows = _stress_cell(names, seeds, wd / "base")
        base_stats = _stress_stats(base_rows)
        results = {"baseline": base_stats, "series": {}}

        for var, side, levels in STRESS_SERIES:
            cells = []
            for lv in levels:
                is_def = _is_default(var, side, lv)
                label = _lv_label(lv, is_def)
                if is_def:
                    stats = base_stats
                else:
                    over = {var: lv}
                    kw = ({"art_over": over} if side == "art"
                          else {"physio_over": over})
                    rows = _stress_cell(names, seeds, wd / var / f"{lv}", **kw)
                    stats = _stress_stats(rows)
                cells.append({"label": label, "level": lv, "stats": stats})
            results["series"][var] = {"levels": cells,
                                      "break_level": _break_level(cells)}

        # 最坏组合
        combo_rows = _stress_cell(
            names, seeds, wd / "worst",
            art_over=WORST_COMBO["art"],
            physio_over=WORST_COMBO["physio"])
        combo_stats = _stress_stats(combo_rows)
        results["series"]["worst_combo"] = {
            "levels": [
                {"label": "默认", "level": None, "stats": base_stats},
                {"label": "最坏组合", "level": None, "stats": combo_stats},
            ],
            "break_level": None,
        }

        if make_fig:
            _plot_stress(results, FIG_DIR / "sweep_stress.png")
    return results


def _break_level(cells) -> str | None:
    """断点：接受率 <0.8 或 MAE >2 mmHg 的第一档。"""
    for cell in cells:
        s = cell["stats"]
        if s["steady_accept_rate"] is None:
            continue
        mae = max(s["mae_sbp"] or 0.0, s["mae_dbp"] or 0.0)
        if s["steady_accept_rate"] < 0.8 or mae > 2.0:
            return cell["label"]
    return None


def _plot_stress(res: dict, path: Path) -> Path:
    fig, axes = plt.subplots(2, 3, figsize=(14, 8), constrained_layout=True)
    numeric = ["noise_sigma", "breathing_amp", "beat_jitter", "deflate_rate"]
    title_map = {"noise_sigma": "传感器白噪声 σ", "breathing_amp": "呼吸幅度",
                 "beat_jitter": "心率抖动", "deflate_rate": "放气速率",
                 "mains_on": "工频干扰", "worst_combo": "最坏组合"}
    for ax, var in zip(axes.flat, numeric):
        cells = res["series"][var]["levels"]
        xs = [c["level"] for c in cells]
        es = [c["stats"]["mae_sbp"] or 0 for c in cells]
        ed = [c["stats"]["mae_dbp"] or 0 for c in cells]
        accr = [c["stats"]["steady_accept_rate"] or 0 for c in cells]
        ax.plot(xs, es, "o-", color="#bb3333", lw=1.2, label="MAE SBP")
        ax.plot(xs, ed, "s-", color="#337733", lw=1.2, label="MAE DBP")
        ax.set_title(f"{title_map[var]}", fontsize=10)
        ax.set_xlabel(var)
        ax.set_ylabel("MAE [mmHg]")
        ax2 = ax.twinx()
        ax2.plot(xs, accr, "^--", color="#777777", lw=0.9, label="接受率")
        ax2.set_ylim(-0.05, 1.05)
        ax2.set_ylabel("接受率", color="#777777", fontsize=8)
        ax2.tick_params(axis="y", labelsize=8)
        brk = res["series"][var]["break_level"]
        if brk:
            xb = [c["level"] for c in cells if c["label"] == brk][0]
            ax.axvline(xb, color="#000000", ls=":", lw=1.0)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="lower left")
    # 工频 / 最坏组合：柱状对比
    for ax, var in zip([axes.flat[4], axes.flat[5]], ["mains_on", "worst_combo"]):
        cells = res["series"][var]["levels"]
        labels = [c["label"] for c in cells]
        es = [c["stats"]["mae_sbp"] or 0 for c in cells]
        ed = [c["stats"]["mae_dbp"] or 0 for c in cells]
        xs = np.arange(len(labels))
        ax.bar(xs - 0.15, es, width=0.3, color="#bb3333", label="MAE SBP")
        ax.bar(xs + 0.15, ed, width=0.3, color="#337733", label="MAE DBP")
        ax.set_xticks(xs, labels)
        for xi, c in enumerate(cells):
            r = c["stats"]["steady_accept_rate"]
            ax.text(xi, max(es[xi], ed[xi]) + 0.05, f"接受率 {r:.0%}",
                    ha="center", fontsize=8)
        ax.set_title(title_map[var], fontsize=10)
        ax.set_ylabel("MAE [mmHg]")
        ax.legend(fontsize=8)
    fig.suptitle("实验二：伪影压力扫描（8 场景 × 5 种子，仅动一个变量）",
                 fontsize=13)
    return _save_fig(fig, path)


# ======================================================================
# 实验三：比率换算率验证（包络复用）
# ======================================================================

def run_ratio(names=None, seeds=SEEDS3, sbp_levels=None, dbp_levels=None,
              make_fig=True, workdir=None) -> dict:
    """实验三：比率在 Flash 范围内扫描；包络复用，仅重跑判定。"""
    names = names or ALL_SCENARIOS
    sbp_levels = sbp_levels or [round(0.40 + 0.02 * i, 2) for i in range(11)]
    dbp_levels = dbp_levels or [round(0.60 + 0.02 * i, 2) for i in range(11)]
    base = AlgoParams()
    with contextlib.ExitStack() as stack:
        wd = (Path(workdir) if workdir
              else Path(stack.enter_context(
                  tempfile.TemporaryDirectory(prefix="sweep_ratio_"))))
        recs = {(name, s): _gen_rec(name, s, wd)
                for name in names for s in seeds}
        # 基准运行一次：包络与有效峰时刻备用（比率不影响两者）
        prepared = {}
        for k, (rec, truth) in recs.items():
            res = run(rec, base)
            peaks_t = [c.t_s for c in accepted_peaks(res.candidates)]
            prepared[k] = (res.envelope, peaks_t, truth)

        results = {"sbp": _ratio_field(prepared, base, "ratio_sbp", sbp_levels),
                   "dbp": _ratio_field(prepared, base, "ratio_dbp", dbp_levels)}

        if make_fig:
            _plot_ratio(results, FIG_DIR / "sweep_ratio.png")
    return results


def _ratio_field(prepared, base, field, levels) -> dict:
    truth_key = "sbp" if field == "ratio_sbp" else "dbp"
    curve, checks = [], []
    for lv in levels:
        p2 = replace(base, **{field: float(lv)})
        errs, per_scen = [], {}
        found = 0
        for k, (env, pt, truth) in prepared.items():
            d = decide(env, pt, p2)
            est = getattr(d, truth_key)
            if est is None:
                continue
            e = float(est - truth[truth_key])
            errs.append(e)
            per_scen.setdefault(k[0], []).append(e)
            found += 1
        mean_e = float(np.mean(errs)) if errs else None
        curve.append({"ratio": float(lv), "mean_err": _r(mean_e),
                      "found": found, "n_total": len(prepared)})
        checks.append((float(lv), mean_e))

    xs = np.array([c[0] for c in checks])
    ys = np.array([c[1] if c[1] is not None else np.nan for c in checks])
    mask = np.isfinite(ys)
    slope = zero = None
    if mask.sum() >= 3:
        a, b = np.polyfit(xs[mask], ys[mask], 1)
        slope = float(a)
        zero = float(-b / a) if abs(a) > 1e-9 else None
    # 每场景零点（线性插值）
    per_zero = {}
    scen_series = {}
    for k, (env, pt, truth) in prepared.items():
        scen_series.setdefault(k[0], [])
    for lv in levels:
        p2 = replace(base, **{field: float(lv)})
        for k, (env, pt, truth) in prepared.items():
            d = decide(env, pt, p2)
            est = getattr(d, truth_key)
            if est is not None:
                scen_series[k[0]].append((float(lv), float(est - truth[truth_key])))
    for name, pts in scen_series.items():
        pts = sorted(pts)
        z = None
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if y1 == 0.0:
                z = x1
                break
            if y1 * y2 < 0:
                z = x1 - y1 * (x2 - x1) / (y2 - y1)
                break
        per_zero[name] = _r(z, 4)
    return {
        "curve": curve,
        "slope_per_unit": _r(slope, 2),
        "slope_per_0.1": _r(slope * 0.1, 3) if slope is not None else None,
        "zero_crossing": _r(zero, 4),
        "zero_crossing_per_scenario": per_zero,
    }


def _plot_ratio(res: dict, path: Path) -> Path:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
    for ax, key, color, title in [
            (ax1, "sbp", "#bb3333", "SBP 侧：ratio_sbp（固定 ratio_dbp=0.70）"),
            (ax2, "dbp", "#337733", "DBP 侧：ratio_dbp（固定 ratio_sbp=0.50）")]:
        xs = [c["ratio"] for c in res[key]["curve"]]
        ys = [c["mean_err"] if c["mean_err"] is not None else np.nan
              for c in res[key]["curve"]]
        ax.plot(xs, ys, "o-", color=color, lw=1.2)
        ax.axhline(0.0, color="#999999", lw=0.8)
        z = res[key]["zero_crossing"]
        if z is not None:
            ax.axvline(z, color="#000000", ls=":", lw=1.0)
            ax.text(z, ax.get_ylim()[1] * 0.9, f" 零点 {z:.4f}", fontsize=9)
        s = res[key]["slope_per_0.1"]
        if s is not None:
            ax.text(0.03, 0.05, f"斜率 ≈ {s:+.2f} mmHg / 0.1 比率",
                    transform=ax.transAxes, fontsize=9)
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("比率")
        ax.set_ylabel("平均误差 [mmHg]")
    fig.suptitle("实验三：比率换算率验证（对齐基准：0.50/0.70 处误差≈0）",
                 fontsize=13)
    return _save_fig(fig, path)


# ======================================================================
# 实验四：仿真器生理与机动参数
# ======================================================================

def envelope_markers(physio: PhysioParams) -> dict:
    """给定生理参数的解析包络特征：峰值位置 k、边缘比率 r_s / r_d。"""
    pc = np.linspace(physio.dbp - 60.0, physio.sbp + 60.0, 24001)
    a = np.asarray(model.beat_amplitude(pc, physio), dtype=float)
    i = int(np.argmax(a))
    amax = float(a[i])
    r_s = float(model.beat_amplitude(np.array([physio.sbp]), physio)[0]) / amax
    r_d = float(model.beat_amplitude(np.array([physio.dbp]), physio)[0]) / amax
    k = (float(pc[i]) - physio.dbp) / physio.pp
    return {"pc_peak": _r(float(pc[i]), 1), "amax": _r(amax, 3),
            "r_s": _r(r_s, 4), "r_d": _r(r_d, 4), "k": _r(k, 4)}


def run_sim(make_fig=True, workdir=None) -> dict:
    """实验四：形状 / 幅度与分辨率 / 速率×心率 / 预充充裕度。"""
    base = PhysioParams()
    out = {}
    with contextlib.ExitStack() as stack:
        wd = (Path(workdir) if workdir
              else Path(stack.enter_context(
                  tempfile.TemporaryDirectory(prefix="sweep_sim_"))))
        out["shape"] = _sim_shape(wd / "shape")
        out["ampres"] = _sim_ampres(wd / "ampres")
        out["rate_hr"] = _sim_rate_hr(wd / "rate_hr")
        out["precharge"] = _sim_precharge(wd / "precharge")
        if make_fig:
            _plot_sim(out, FIG_DIR / "sweep_sim.png")
    return out


def _sim_shape(wd: Path) -> dict:
    base = PhysioParams()
    configs = {
        "基准": {},
        "σn×0.7": {"sigma_n_r": base.sigma_n_r * 0.7},
        "σn×1.4": {"sigma_n_r": base.sigma_n_r * 1.4},
        "σp×0.7": {"sigma_p_r": base.sigma_p_r * 0.7},
        "σp×1.4": {"sigma_p_r": base.sigma_p_r * 1.4},
        "μ×0.7": {"mu_r": base.mu_r * 0.7},
        "μ×1.4": {"mu_r": base.mu_r * 1.4},
        "全部×0.7": {"sigma_n_r": base.sigma_n_r * 0.7,
                     "sigma_p_r": base.sigma_p_r * 0.7, "mu_r": base.mu_r * 0.7},
        "全部×1.4": {"sigma_n_r": base.sigma_n_r * 1.4,
                     "sigma_p_r": base.sigma_p_r * 1.4, "mu_r": base.mu_r * 1.4},
    }
    results = {}
    for cfg, over in configs.items():
        markers, errs_s, errs_d, accepts = None, [], [], []
        for name in ("normal", "wide_pp"):
            physio = replace(scenarios.get_scenario(name)[0], **over)
            markers = envelope_markers(physio)
            for s in SEEDS3:
                rec, truth = _gen_rec(name, s, wd / cfg / name, physio=physio)
                m = _metrics(run(rec), truth)
                accepts.append(m["accepted"])
                if m["accepted"] and m["err_sbp"] is not None:
                    errs_s.append(abs(m["err_sbp"]))
                    errs_d.append(abs(m["err_dbp"]))
        results[cfg] = {
            "overrides": {k: _r(v, 5) for k, v in over.items()},
            "markers": markers,
            "mae_sbp": _r(np.mean(errs_s)) if errs_s else None,
            "mae_dbp": _r(np.mean(errs_d)) if errs_d else None,
            "accept_rate": _r(np.mean(accepts)),
        }
    return results


def _sim_ampres(wd: Path) -> dict:
    configs = [
        ("基准", {}, None),
        ("k_cuff=10", {"k_cuff": 10.0}, None),
        ("k_cuff=40", {"k_cuff": 40.0}, None),
        ("LSB=0.05", {}, 0.05),
        ("LSB=0.2", {}, 0.2),
        ("k=40+LSB=0.2", {"k_cuff": 40.0}, 0.2),
    ]
    results = {}
    for label, over, lsb in configs:
        markers, errs_s, errs_d, accepts = None, [], [], []
        for name in ("normal", "bradycardia"):
            physio = replace(scenarios.get_scenario(name)[0], **over)
            markers = envelope_markers(physio)
            for s in SEEDS3:
                rec, truth = _gen_rec(name, s, wd / label / name,
                                      physio=physio, lsb=lsb)
                m = _metrics(run(rec), truth)
                accepts.append(m["accepted"])
                if m["accepted"] and m["err_sbp"] is not None:
                    errs_s.append(abs(m["err_sbp"]))
                    errs_d.append(abs(m["err_dbp"]))
        results[label] = {
            "amax": markers["amax"],
            "lsb": lsb if lsb is not None else 0.1,
            "mae_sbp": _r(np.mean(errs_s)) if errs_s else None,
            "mae_dbp": _r(np.mean(errs_d)) if errs_d else None,
            "accept_rate": _r(np.mean(accepts)),
        }
    return results


def _sim_rate_hr(wd: Path) -> dict:
    cells = []
    for rate in (2.0, 3.0, 5.0):
        for hr in (40.0, 70.0, 110.0, 180.0):
            physio = replace(scenarios.get_scenario("normal")[0],
                             deflate_rate=rate, hr=hr)
            errs_s, errs_d, accepts, pts = [], [], [], []
            for s in SEEDS3:
                rec, truth = _gen_rec("normal", s, wd / f"r{rate:g}_h{hr:g}",
                                      physio=physio)
                m = _metrics(run(rec), truth)
                accepts.append(m["accepted"])
                if m["accepted"] and m["err_sbp"] is not None:
                    errs_s.append(m["err_sbp"])
                    errs_d.append(m["err_dbp"])
                    pts.append(m["n_points"])
            cells.append({
                "rate": rate, "hr": hr,
                "err_sbp": _r(np.mean(errs_s)) if errs_s else None,
                "err_dbp": _r(np.mean(errs_d)) if errs_d else None,
                "accept_rate": _r(np.mean(accepts)),
                "n_points": _r(np.mean(pts), 1) if pts else None,
            })
    return {"cells": cells}


def _sim_precharge(wd: Path) -> dict:
    cells = []
    min_margin = {}
    for sbp in (90.0, 120.0, 150.0, 180.0, 190.0):
        ok_margins = []
        for margin in (10.0, 20.0, 30.0, 40.0):
            physio = replace(PhysioParams(), sbp=sbp, dbp=sbp - 40.0,
                             p_target=sbp + margin)
            accepts, reasons = [], []
            for s in SEEDS3:
                rec, truth = _gen_rec("normal", s,
                                      wd / f"s{sbp:g}_m{margin:g}",
                                      physio=physio)
                m = _metrics(run(rec), truth)
                accepts.append(m["accepted"])
                reasons += m["reasons"]
            rate = float(np.mean(accepts))
            cells.append({"sbp": sbp, "margin": margin,
                          "accept_rate": _r(rate),
                          "reasons": sorted(set(reasons))})
            if rate >= 1.0:
                ok_margins.append(margin)
        min_margin[str(int(sbp))] = min(ok_margins) if ok_margins else None
    return {"cells": cells, "min_sufficient_margin": min_margin}


def _plot_sim(res: dict, path: Path) -> Path:
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)

    # (a) 形状失配：隐含边缘比率 vs 误差
    ax = axes[0, 0]
    for cfg, e in res["shape"].items():
        r_s, r_d = e["markers"]["r_s"], e["markers"]["r_d"]
        ax.scatter(r_s, e["mae_sbp"] or 0, color="#bb3333", zorder=3)
        ax.scatter(r_d, e["mae_dbp"] or 0, color="#337733", zorder=3)
        ax.annotate(cfg, (r_s, e["mae_sbp"] or 0), fontsize=7,
                    xytext=(3, 3), textcoords="offset points")
    ax.axvline(0.50, color="#bb3333", ls=":", lw=0.9)
    ax.axvline(0.70, color="#337733", ls=":", lw=0.9)
    ax.set_xlabel("隐含边缘比率（红点=r_s，绿点=r_d）")
    ax.set_ylabel("MAE [mmHg]")
    ax.set_title("形状失配 → 误差映射（normal + wide_pp）", fontsize=10)

    # (b) 幅度与分辨率
    ax = axes[0, 1]
    labels = list(res["ampres"])
    xs = np.arange(len(labels))
    es = [res["ampres"][l]["mae_sbp"] or 0 for l in labels]
    ed = [res["ampres"][l]["mae_dbp"] or 0 for l in labels]
    ax.bar(xs - 0.15, es, width=0.3, color="#bb3333", label="MAE SBP")
    ax.bar(xs + 0.15, ed, width=0.3, color="#337733", label="MAE DBP")
    for xi, l in enumerate(labels):
        e = res["ampres"][l]
        ax.text(xi, max(es[xi], ed[xi]) + 0.03,
                f"Amax≈{e['amax']:.1f}\n接受{e['accept_rate']:.0%}",
                ha="center", fontsize=7)
    ax.set_xticks(xs, labels, fontsize=8, rotation=15)
    ax.set_ylabel("MAE [mmHg]")
    ax.set_title("幅度尺度（k_cuff）与量化（LSB）", fontsize=10)
    ax.legend(fontsize=8)

    # (c) 速率 × 心率 热力图
    ax = axes[1, 0]
    rates = [2.0, 3.0, 5.0]
    hrs = [40.0, 70.0, 110.0, 180.0]
    grid = np.full((len(rates), len(hrs)), np.nan)
    for c in res["rate_hr"]["cells"]:
        i = rates.index(c["rate"])
        j = hrs.index(c["hr"])
        vals = [abs(c["err_sbp"] or 0), abs(c["err_dbp"] or 0)]
        grid[i, j] = max(vals)
    im = ax.imshow(grid, cmap="YlOrRd", aspect="auto")
    for i in range(len(rates)):
        for j in range(len(hrs)):
            c = next(c for c in res["rate_hr"]["cells"]
                     if c["rate"] == rates[i] and c["hr"] == hrs[j])
            es = "--" if c["err_sbp"] is None else f"{c['err_sbp']:+.1f}"
            ed = "--" if c["err_dbp"] is None else f"{c['err_dbp']:+.1f}"
            ax.text(j, i, f"{es}/{ed}",
                    ha="center", va="center", fontsize=8)
    ax.set_xticks(range(len(hrs)), [f"{h:g}" for h in hrs])
    ax.set_yticks(range(len(rates)), [f"{r:g}" for r in rates])
    ax.set_xlabel("心率 [bpm]")
    ax.set_ylabel("放气速率 [mmHg/s]")
    ax.set_title("误差 SBP/DBP [mmHg]（速率×心率交互）", fontsize=10)
    fig.colorbar(im, ax=ax, shrink=0.8)

    # (d) 预充充裕度
    ax = axes[1, 1]
    marg = [10.0, 20.0, 30.0, 40.0]
    sbps = [90.0, 120.0, 150.0, 180.0, 190.0]
    grid2 = np.full((len(sbps), len(marg)), np.nan)
    for c in res["precharge"]["cells"]:
        i = sbps.index(c["sbp"])
        j = marg.index(c["margin"])
        grid2[i, j] = c["accept_rate"]
    im2 = ax.imshow(grid2, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    for i in range(len(sbps)):
        for j in range(len(marg)):
            ok = grid2[i, j] >= 1.0
            ax.text(j, i, "OK" if ok else "不足", ha="center", va="center",
                    fontsize=8)
    ax.set_xticks(range(len(marg)), [f"{m:g}" for m in marg])
    ax.set_yticks(range(len(sbps)), [f"{s:g}" for s in sbps])
    ax.set_xlabel("预充余量（p_target − SBP）[mmHg]")
    ax.set_ylabel("SBP [mmHg]")
    ax.set_title("预充充裕度（全部种子接受=OK）", fontsize=10)
    fig.colorbar(im2, ax=ax, shrink=0.8)

    fig.suptitle("实验四：仿真器生理与机动参数扫描", fontsize=13)
    return _save_fig(fig, path)


# ======================================================================
# CLI
# ======================================================================

def _print_algo(res: dict) -> None:
    print(f"基准: MAE {res['baseline']['mae_sbp']}/{res['baseline']['mae_dbp']} mmHg"
          f"，接受 {res['baseline']['n_accepted']}/{res['baseline']['n_total']}")
    for p, e in res["params"].items():
        fr, fa = len(e["false_reject"]), len(e["false_accept"])
        print(f"  {p:<14} [{e['verdict']}] "
              f"maxΔ {e['max_delta_sbp']}/{e['max_delta_dbp']} mmHg, "
              f"{e['max_delta_hr']} bpm | 误拒{fr} 误收{fa}")


def _print_stress(res: dict) -> None:
    for var, e in res["series"].items():
        brk = e["break_level"] or "—"
        last = e["levels"][-1]["stats"]
        print(f"  {var:<14} 断点 {brk:<10} "
              f"末档 MAE {last['mae_sbp']}/{last['mae_dbp']} "
              f"接受率 {last['steady_accept_rate']}")


def _print_ratio(res: dict) -> None:
    for key, cn in (("sbp", "SBP"), ("dbp", "DBP")):
        e = res[key]
        print(f"  {cn}: 零点 {e['zero_crossing']} | "
              f"斜率 {e['slope_per_0.1']} mmHg / 0.1 比率")


def _print_sim(res: dict) -> None:
    print("  形状失配:")
    for cfg, e in res["shape"].items():
        m = e["markers"]
        print(f"    {cfg:<10} r_s={m['r_s']:.4f} r_d={m['r_d']:.4f} "
              f"k={m['k']:.4f} → MAE {e['mae_sbp']}/{e['mae_dbp']}")
    print("  预充最低充足余量: "
          + ", ".join(f"SBP{s}:{m or '>40'}"
                      for s, m in res["precharge"]["min_sufficient_margin"].items()))


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        # Windows 控制台 GBK 编码无法输出 σₙ 等字符：强制 UTF-8（文件输出不受影响）
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="参数扫描实验（四模式）")
    parser.add_argument("--mode", required=True,
                        choices=["algo", "stress", "ratio", "sim"])
    args = parser.parse_args(argv)

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    runners = {"algo": run_algo, "stress": run_stress,
               "ratio": run_ratio, "sim": run_sim}
    res = runners[args.mode]()

    out_path = EVAL_DIR / f"sweep_{args.mode}.json"
    out_path.write_text(json.dumps(res, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    {"algo": _print_algo, "stress": _print_stress,
     "ratio": _print_ratio, "sim": _print_sim}[args.mode](res)
    print(f"[ok] 结果已写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
