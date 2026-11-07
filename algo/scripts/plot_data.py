"""仿真数据可视化：读契约 CSV → 波形图 PNG（out/figs/）。

用法（在 algo/ 目录下，先激活 .venv）：
  python scripts/plot_data.py --scenario normal --seed 1
  python scripts/plot_data.py --all --seeds 1
  python scripts/plot_data.py --csv data/sim/normal_s001.csv

说明：图中「包络」为可视化粗算（滤波＋峰检测近似），仅用于观察信号形态与
比率机制；正式判定（比率/质控）以 bp/ 管线为准。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
from scipy.signal import butter, find_peaks, sosfiltfilt

from bp.csvio import read_csv
from sim.generate import generate
from sim.scenarios import SCENARIOS

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "sim"
FIG_DIR = ROOT / "out" / "figs"
FS = 80.0

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

PHASE_STYLE = {
    "INFLATE": ("#cfe3f7", "充气"),
    "SETTLE": ("#e8e8e8", "静置"),
    "DEFLATE": ("#d8f0d8", "放气"),
    "DUMP": ("#f8d8d8", "快排"),
}


def filter_dc_ac(p: np.ndarray):
    """DC（0.5 Hz 低通）/ AC（0.5–8 Hz 带通），零相位滤波。"""
    sos_dc = butter(2, 0.5, btype="lowpass", fs=FS, output="sos")
    sos_ac = butter(4, [0.5, 8.0], btype="bandpass", fs=FS, output="sos")
    return sosfiltfilt(sos_dc, p), sosfiltfilt(sos_ac, p)


def crude_envelope(t: np.ndarray, dc: np.ndarray, ac: np.ndarray):
    """可视化粗包络（非管线）：峰检测 + 逐搏 ptp。

    输入应为 **DEFLATE 段切片**（调用方负责掩码）；返回 (pc, amp, (SYS估, DIA估))。
    """
    peaks, _ = find_peaks(ac, height=0.3, distance=int(0.3 * FS))
    if peaks.size < 4:
        return np.array([]), np.array([]), (None, None)
    mids = (peaks[:-1] + peaks[1:]) // 2
    pc_list, amp_list = [], []
    for i, pk in enumerate(peaks):
        lo = mids[i - 1] if i > 0 else 0
        hi = mids[i] if i < mids.size else ac.size - 1
        if hi <= lo:
            continue
        pc_list.append(float(np.mean(dc[lo:hi])))
        amp_list.append(float(np.ptp(ac[lo:hi])))
    pc = np.array(pc_list)
    amp = np.array(amp_list)
    if pc.size < 4:
        return np.array([]), np.array([]), (None, None)

    amax = amp.max()
    order = np.argsort(pc)
    xs, ys = pc[order], amp[order]

    def crossing(level):
        xc = []
        for i in range(xs.size - 1):
            y1, y2 = ys[i], ys[i + 1]
            if (y1 - level) * (y2 - level) <= 0 and y1 != y2:
                xc.append(xs[i] + (level - y1) * (xs[i + 1] - xs[i]) / (y2 - y1))
        return xc

    c5 = crossing(0.5 * amax)
    c7 = crossing(0.7 * amax)
    return pc, amp, (max(c5) if c5 else None, min(c7) if c7 else None)


def plot_recording(rec, truth, stem: str, outdir: Path) -> Path:
    """绘制三面板波形图并保存 PNG；返回文件路径。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    t = rec.t_ms / 1000.0
    p = rec.p_raw
    dc, ac = filter_dc_ac(p)

    # 包络：仅 DEFLATE 段，切片后单独滤波（避免 DUMP 边沿振铃污染），两端留余量
    m_def = rec.phase == "DEFLATE"
    dc_def, ac_def = filter_dc_ac(p[m_def])
    margin = int(0.5 * FS)
    pc_e, amp_e, (sys_est, dia_est) = crude_envelope(
        t[m_def][margin:-margin], dc_def[margin:-margin], ac_def[margin:-margin])

    fig, axes = plt.subplots(3, 1, figsize=(12, 11), constrained_layout=True)

    # 面板 1：全览（相位底纹）
    ax = axes[0]
    for ph, (color, _) in PHASE_STYLE.items():
        m = rec.phase == ph
        if m.any():
            ax.axvspan(t[m][0], t[m][-1], color=color, alpha=0.6)
    ax.plot(t, p, lw=0.6, color="#1a1a1a")
    ax.set_title(f"{stem}：全览（{t[-1]:.1f} s，80 Hz 采样）")
    ax.set_xlabel("时间 [s]")
    ax.set_ylabel("袖带压 [mmHg]")
    ax.set_xlim(t[0], t[-1])
    ax.legend(handles=[Patch(facecolor=c, alpha=0.6, label=l)
                       for c, l in PHASE_STYLE.values()],
              loc="upper right", ncol=4, fontsize=9)

    # 面板 2：DEFLATE 段放大（观察振荡波）
    ax = axes[1]
    m = rec.phase == "DEFLATE"
    n = int(m.sum())
    if truth is not None:
        k = int(np.argmin(np.abs(dc[m] - truth.get("map", 100.0))))
    else:
        k = n // 2
    lo = max(0, k - int(2.5 * FS))
    hi = min(n, k + int(2.5 * FS))
    tm, pm, dm = t[m][lo:hi], p[m][lo:hi], dc[m][lo:hi]
    ax.plot(tm, pm, lw=0.8, color="#1a1a1a", label="p_raw")
    ax.plot(tm, dm, lw=0.8, color="#e07b00", label="DC（0.5 Hz 低通）")
    ax.set_title("DEFLATE 段放大（±2.5 s）：DC 载波 + 逐搏振荡")
    ax.set_xlabel("时间 [s]")
    ax.set_ylabel("袖带压 [mmHg]")
    ax.legend(loc="upper right", fontsize=9)

    # 面板 3：包络粗算
    ax = axes[2]
    if pc_e.size:
        ax.scatter(pc_e, amp_e, s=20, color="#2255aa", zorder=3, label="逐搏幅度")
        order = np.argsort(pc_e)
        ax.plot(pc_e[order], amp_e[order], lw=0.8, color="#2255aa", alpha=0.6)
        amax = amp_e.max()
        ax.axhline(0.5 * amax, color="#bb3333", ls="--", lw=0.9)
        ax.axhline(0.7 * amax, color="#337733", ls="--", lw=0.9)
        if truth is not None:
            for val, c, lab in [(truth["sbp"], "#bb3333", "SBP"),
                                (truth["map"], "#777777", "MAP"),
                                (truth["dbp"], "#337733", "DBP")]:
                ax.axvline(val, color=c, ls=":", lw=0.9)
                ax.text(val, 0.02 * amax, f" {lab} {val:.0f}", color=c, fontsize=9)
        ax.invert_xaxis()  # 横轴 = 放气方向（高压→低压）
        title = "包络粗算（横轴 = 放气方向）"
        if sys_est is not None and dia_est is not None:
            title += f"｜0.50/0.70 交叉估：SYS≈{sys_est:.0f}，DIA≈{dia_est:.0f}"
        ax.set_title(title)
    else:
        ax.text(0.5, 0.5, "有效搏动不足，无法绘制包络",
                ha="center", va="center", transform=ax.transAxes)
    ax.set_xlabel("袖带压 [mmHg]")
    ax.set_ylabel("振荡幅度 [mmHg]")

    if truth is not None:
        fig.suptitle(f"仿真波形 {stem}｜真值 {truth['sbp']:.0f}/{truth['dbp']:.0f} mmHg"
                     f"，{truth['hr']:.0f} bpm", fontsize=13)
    path = outdir / f"{stem}.png"
    try:
        fig.savefig(path, dpi=140)
    except PermissionError:
        # 文件被查看器占用时（Windows 常见）：改存 _alt 后缀，不中断批次
        path = outdir / f"{stem}_alt.png"
        fig.savefig(path, dpi=140)
        print(f"[warn] {stem}.png locked (viewer?), wrote {path.name}")
    plt.close(fig)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="仿真数据可视化（波形图 PNG）")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--seeds", type=int, nargs="+", help="配合 --all 的 seed 列表")
    parser.add_argument("--all", action="store_true", help="绘制全部场景")
    parser.add_argument("--csv", type=Path, help="直接绘制已有 CSV（跳过生成）")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out", type=Path, default=FIG_DIR)
    args = parser.parse_args(argv)

    jobs = []  # (stem, csv_path, truth)
    if args.csv:
        truth_path = args.csv.with_suffix(".truth.json")
        truth = (json.loads(truth_path.read_text(encoding="utf-8"))
                 if truth_path.exists() else None)
        jobs.append((args.csv.stem, args.csv, truth))
    else:
        seeds = args.seeds or [args.seed]
        names = sorted(SCENARIOS) if args.all else [args.scenario or "normal"]
        for name in names:
            for s in seeds:
                csv_path, truth_path = generate(name, s, args.data_dir)
                jobs.append((f"{name}_s{s:03d}", csv_path,
                             json.loads(truth_path.read_text(encoding="utf-8"))))

    for stem, csv_path, truth in jobs:
        rec = read_csv(csv_path)
        path = plot_recording(rec, truth, stem, args.out)
        print(f"[ok] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
