"""对 CSV 跑完整算法管线：打印结果；（可选）与真值对照并输出误差表。

用法（在 algo/ 目录下，先激活 .venv）：
  python scripts/run_pipeline.py --scenario normal --seed 1
  python scripts/run_pipeline.py --all --eval
  python scripts/run_pipeline.py --csv data/sim/normal_s001.csv --eval
  python scripts/run_pipeline.py --all --eval --fs 40     # 采样率扫描

说明：真值仅在 **本脚本** 读取用于评估（--eval）；bp 管线本身不接触真值。
"""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from bp.params import AlgoParams
from bp.pipeline import run_csv
from sim.generate import generate
from sim.scenarios import SCENARIOS

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "sim"
EVAL_DIR = ROOT / "out" / "eval"


def _fmt(x, spec=".1f") -> str:
    return "--" if x is None else format(x, spec)


def _run_one(csv_path: Path, params: AlgoParams, truth: dict | None) -> dict:
    res = run_csv(csv_path, params)
    d = res.decision
    row = {
        "stem": csv_path.stem,
        "accepted": bool(res.accepted),
        "sbp": d.sbp, "dbp": d.dbp, "map": d.map, "hr": d.hr,
        "dia_flag": bool(d.dia_flag), "arrhythmia": bool(d.arrhythmia),
        "n_peaks": res.n_accepted,
        "n_points": res.envelope.n_points,
        "env_cv": res.qc.env_cv,
        "outlier_ratio": res.qc.outlier_ratio,
        "reasons": list(res.qc.reasons),
    }
    if truth is not None:
        for key in ("sbp", "dbp", "hr"):
            est, true = row[key], truth.get(key)
            row[f"err_{key}"] = None if (est is None or true is None) else est - true
    return row


def _print_row(row: dict, truth: dict | None) -> None:
    line = (f"{row['stem']:>18} | {'OK ' if row['accepted'] else 'REJ'} "
            f"| {_fmt(row['sbp']):>6}/{_fmt(row['dbp']):>6} MAP {_fmt(row['map']):>6} "
            f"| HR {_fmt(row['hr'])} | pts {row['n_points']:>2} "
            f"| cv {_fmt(row['env_cv'], '.3f')}")
    if truth is not None:
        line += (f" | err {_fmt(row.get('err_sbp'), '+.1f'):>7}"
                 f"/{_fmt(row.get('err_dbp'), '+.1f'):>7}"
                 f" | truth {truth['sbp']:.0f}/{truth['dbp']:.0f} HR{truth['hr']:.0f}")
    print(line)
    if row["reasons"]:
        print(f"{'':>18}   reasons: {'; '.join(row['reasons'])}")
    if row["dia_flag"]:
        print(f"{'':>18}   [flag] DIA 交叉校验超差")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="示波法血压算法管线（PC 原型）")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--seeds", type=int, nargs="+", help="配合 --all 的 seed 列表")
    parser.add_argument("--all", action="store_true", help="全部场景")
    parser.add_argument("--csv", type=Path, help="直接跑已有 CSV（跳过生成）")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--fs", type=float, default=80.0, help="输出采样率 [Hz]（默认 80）")
    parser.add_argument("--eval", action="store_true", help="与真值对照（仅脚本侧读取）")
    parser.add_argument("--out", type=Path, default=EVAL_DIR,
                        help="--all --eval 时误差表 JSON 输出目录")
    args = parser.parse_args(argv)

    params = AlgoParams()
    if args.fs != params.fs:
        params = replace(params, fs=args.fs)

    jobs: list[tuple[Path, dict | None]] = []
    if args.csv:
        truth_path = args.csv.with_suffix(".truth.json")
        truth = (json.loads(truth_path.read_text(encoding="utf-8"))
                 if truth_path.exists() else None)
        jobs.append((args.csv, truth))
    else:
        seeds = args.seeds or [args.seed]
        names = sorted(SCENARIOS) if args.all else [args.scenario or "normal"]
        for name in names:
            for s in seeds:
                csv_path, truth_path = generate(name, s, args.data_dir,
                                                fs_out=args.fs)
                truth = (json.loads(truth_path.read_text(encoding="utf-8"))
                         if args.eval else None)
                jobs.append((csv_path, truth))

    rows = []
    for csv_path, truth in jobs:
        row = _run_one(csv_path, params, truth)
        _print_row(row, truth)
        rows.append(row)

    if args.eval and args.all:
        args.out.mkdir(parents=True, exist_ok=True)
        out_path = args.out / "pipeline_eval.json"
        out_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        ok_rows = [r for r in rows if r["accepted"] and r.get("err_sbp") is not None]
        if ok_rows:
            mae_sbp = float(np.mean([abs(r["err_sbp"]) for r in ok_rows]))
            mae_dbp = float(np.mean([abs(r["err_dbp"]) for r in ok_rows]))
            print(f"\n汇总（{len(ok_rows)}/{len(rows)} 接受）:"
                  f" MAE SBP {mae_sbp:.1f} mmHg, MAE DBP {mae_dbp:.1f} mmHg")
        print(f"[ok] 误差表已写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
