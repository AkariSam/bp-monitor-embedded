"""AC 分量频域分析：读契约 CSV → 频谱图 PNG（out/figs/<stem>_spec.png）。

三面板：
  1. AC 频谱（MAP 附近 ±10 s 稳态窗）：脉搏基频与谐波、呼吸峰、设计带通边界
  2. 全 DEFLATE 段频谱图（时间-频率）：看脉搏带能量随放气过程涨落
  3. PSD 对比：MAP 窗 vs 放气起始（高压低振荡）段 + 噪声底标注

用法（在 algo/ 目录下，先激活 .venv）：
  python scripts/plot_spectrum.py --scenario normal --seed 1
  python scripts/plot_spectrum.py --all --seeds 1
  python scripts/plot_spectrum.py --csv data/sim/normal_s001.csv
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import butter, periodogram, sosfiltfilt, spectrogram

from bp.csvio import read_csv
from plot_data import DATA_DIR, FIG_DIR, FS
from sim.generate import generate
from sim.scenarios import SCENARIOS

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False


def ac_component(p: np.ndarray):
    """交流分量 = p − DC（0.5 Hz 低通）；返回 (dc, ac)。"""
    sos_dc = butter(2, 0.5, btype="lowpass", fs=FS, output="sos")
    dc = sosfiltfilt(sos_dc, p)
    return dc, p - dc


def to_db(psd: np.ndarray) -> np.ndarray:
    """功率谱密度 → dB（参考 1 mmHg²/Hz）。"""
    return 10.0 * np.log10(np.maximum(psd, 1e-12))


def hr_from_psd(f: np.ndarray, psd: np.ndarray, fmin=0.6, fmax=3.5):
    """PSD 峰值（抛物线插值）→ (HR [bpm], f0 [Hz])。"""
    m = (f >= fmin) & (f <= fmax)
    idx = int(np.flatnonzero(m)[np.argmax(psd[m])])
    if 0 < idx < len(f) - 1:
        y0, y1, y2 = psd[idx - 1], psd[idx], psd[idx + 1]
        denom = y0 - 2.0 * y1 + y2
        d = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
        f0 = f[idx] + d * (f[idx + 1] - f[idx])
    else:
        f0 = f[idx]
    return f0 * 60.0, f0


def plot_spectrum(rec, truth, stem: str, outdir: Path) -> Path:
    """绘制三面板频谱分析图并保存 PNG；返回文件路径。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    t = rec.t_ms / 1000.0
    m = rec.phase == "DEFLATE"
    td, pd = t[m], rec.p_raw[m]
    dc, ac = ac_component(pd)

    # 稳态窗：MAP 附近 ±10 s（无真值时取中段）
    k = (int(np.argmin(np.abs(dc - truth["map"]))) if truth is not None
         else len(pd) // 2)
    half = int(10 * FS)
    lo, hi = max(0, k - half), min(len(pd), k + half)
    f, psd_mid = periodogram(ac[lo:hi], fs=FS, window="hann", nfft=8192)

    hr_est, f0 = hr_from_psd(f, psd_mid)
    f0_ref = truth["hr"] / 60.0 if truth is not None else f0
    f_breath = (truth.get("artifacts", {}).get("breathing_hz", 0.25)
                if truth is not None else 0.25)
    f_cut = f <= 15.0

    fig, axes = plt.subplots(3, 1, figsize=(12, 12), constrained_layout=True)

    # 面板 1：AC 频谱（稳态窗）
    ax = axes[0]
    ax.plot(f[f_cut], to_db(psd_mid[f_cut]), lw=0.9, color="#2255aa", label="AC PSD")
    ax.axvline(f0_ref, color="#bb3333", ls="--", lw=1.0,
               label=f"脉搏基频 {f0_ref:.2f} Hz")
    for kk in range(2, int(8.0 / f0_ref) + 1):
        ax.axvline(kk * f0_ref, color="#bb3333", ls=":", lw=0.7, alpha=0.6)
    ax.axvline(f_breath, color="#337733", ls="--", lw=1.0,
               label=f"呼吸 {f_breath:.2f} Hz")
    for edge in (0.5, 8.0):
        ax.axvline(edge, color="#888888", ls="-.", lw=0.9)
    ax.text(8.05, ax.get_ylim()[0] + 3, " 设计带通 0.5–8 Hz", color="#666666", fontsize=9)
    ax.set_title(f"AC 频谱（MAP ±10 s 稳态窗）｜频域 HR 估计 {hr_est:.0f} bpm"
                 + (f"（真值 {truth['hr']:.0f}）" if truth is not None else ""))
    ax.set_xlabel("频率 [Hz]")
    ax.set_ylabel("PSD [dB]")
    ax.set_xlim(0, 15)
    ax.legend(loc="upper right", fontsize=9)

    # 面板 2：全 DEFLATE 段频谱图
    ax = axes[1]
    f2, t2, S = spectrogram(ac, fs=FS, nperseg=320, noverlap=240, window="hann")
    pcm = ax.pcolormesh(td[0] + t2, f2, to_db(S), shading="auto", cmap="magma")
    ax.axhline(f0_ref, color="#00ffcc", ls="--", lw=1.0)
    ax.axhline(f_breath, color="#66ff66", ls=":", lw=1.0)
    ax.set_ylim(0, 15)
    ax.set_title("DEFLATE 段频谱图：脉搏带能量随放气涨落（虚线=基频，点线=呼吸）")
    ax.set_xlabel("时间 [s]")
    ax.set_ylabel("频率 [Hz]")
    fig.colorbar(pcm, ax=ax, label="PSD [dB]")

    # 面板 3：PSD 对比（MAP 窗 vs 高压起始段）
    ax = axes[2]
    n_hi = int(2.5 * FS)
    f3, psd_hi = periodogram(ac[:n_hi], fs=FS, window="hann", nfft=8192)
    floor_db = float(np.median(to_db(psd_mid[(f >= 12) & (f <= 38)])))
    ax.plot(f[f_cut], to_db(psd_mid[f_cut]), lw=0.9, color="#2255aa",
            label="MAP 窗（振荡最强）")
    ax.plot(f3[f_cut], to_db(psd_hi[f_cut]), lw=0.9, color="#cc7733",
            label="放气起始段（高压，振荡很弱）")
    ax.axhline(floor_db, color="#888888", ls=":", lw=0.9,
               label=f"噪声底 ≈ {floor_db:.0f} dB (12–38 Hz 中位)")
    ax.set_title("PSD 对比：信号窗 vs 高压窗 vs 噪声底")
    ax.set_xlabel("频率 [Hz]")
    ax.set_ylabel("PSD [dB]")
    ax.set_xlim(0, 15)
    ax.legend(loc="upper right", fontsize=9)

    if truth is not None:
        fig.suptitle(f"AC 频域分析 {stem}｜真值 {truth['sbp']:.0f}/{truth['dbp']:.0f} mmHg"
                     f"，{truth['hr']:.0f} bpm", fontsize=13)
    path = outdir / f"{stem}_spec.png"
    try:
        fig.savefig(path, dpi=140)
    except PermissionError:
        path = outdir / f"{stem}_spec_alt.png"
        fig.savefig(path, dpi=140)
        print(f"[warn] {stem}_spec.png locked (viewer?), wrote {path.name}")
    plt.close(fig)

    print(f"[stat] {stem}: HR_est={hr_est:.1f} bpm"
          + (f" truth={truth['hr']:.1f}" if truth is not None else "")
          + f" floor={floor_db:.1f} dB")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AC 分量频域分析（频谱图 PNG）")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--seeds", type=int, nargs="+", help="配合 --all 的 seed 列表")
    parser.add_argument("--all", action="store_true", help="分析全部场景")
    parser.add_argument("--csv", type=Path, help="直接分析已有 CSV（跳过生成）")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out", type=Path, default=FIG_DIR)
    args = parser.parse_args(argv)

    jobs = []
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
        path = plot_spectrum(rec, truth, stem, args.out)
        print(f"[ok] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
