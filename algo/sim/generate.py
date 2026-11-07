"""生成入口：scenario × seed → CSV（t_ms 浮点）+ 真值 JSON。

复现规则：随机性全部来自 ``SeedSequence(seed)`` 派生出的独立流域
（见 ``RNG_STREAMS``）。流域清单与顺序一旦发布不得改动，否则同 seed 不再
字节级复现。
"""

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from bp.csvio import Recording, write_csv
from sim import artifacts, model, scenarios

# 随机流域清单：顺序即复现契约，勿动。
RNG_STREAMS = ("physio", "noise", "breath")


def sample_grid(t_dense: np.ndarray, fs_in: float, fs_out: float):
    """密集网格 → 输出网格的最近邻索引映射；返回 (t_out [ms], idx)。"""
    n_out = int(np.floor(t_dense[-1] * fs_out)) + 1
    idx = np.round(np.arange(n_out) * (fs_in / fs_out)).astype(int)
    idx = np.clip(idx, 0, t_dense.size - 1)
    t_out_ms = np.arange(n_out) * (1000.0 / fs_out)
    return t_out_ms, idx


def generate(scenario: str, seed: int, outdir: str | Path,
             fs_out: float = model.FS_OUT,
             physio: model.PhysioParams | None = None,
             art: artifacts.ArtifactParams | None = None,
             lsb: float | None = None) -> tuple[Path, Path]:
    """生成一个测量记录；返回 (csv 路径, truth json 路径)。

    ``physio`` / ``art`` / ``lsb`` 用于实验脚本覆盖场景默认值（None = 用场景
    默认）；覆盖后真值 JSON 记录实际使用的参数。默认调用行为与参数覆盖无关，
    同 seed 字节级复现契约不变。
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    default_physio, default_art = scenarios.get_scenario(scenario)
    if physio is None:
        physio = default_physio
    if art is None:
        art = default_art
    if lsb is None:
        lsb = model.LSB

    rngs = dict(zip(RNG_STREAMS,
                    np.random.SeedSequence(seed).spawn(len(RNG_STREAMS))))
    sig = model.synthesize(physio, rng=np.random.default_rng(rngs["physio"]))
    p_dense = artifacts.apply_artifacts(
        sig.p_clean, sig.t, art,
        rng_noise=np.random.default_rng(rngs["noise"]),
        rng_breath=np.random.default_rng(rngs["breath"]),
    )

    t_ms, idx = sample_grid(sig.t, model.FS_SIM, fs_out)
    rec = Recording(
        t_ms=t_ms,
        p_raw=artifacts.quantize(p_dense[idx], lsb),
        phase=sig.phase[idx],
    )

    stem = f"{scenario}_s{seed:03d}"
    csv_path = outdir / f"{stem}.csv"
    truth_path = outdir / f"{stem}.truth.json"
    write_csv(csv_path, rec)

    sn, sp, mu = physio.resolved_shape()
    truth = {
        "scenario": scenario,
        "seed": seed,
        "fs_out": fs_out,
        "sbp": physio.sbp,
        "dbp": physio.dbp,
        "hr": physio.hr,
        "map": physio.map_model,
        "shape_resolved": {"sigma_n": sn, "sigma_p": sp, "mu": mu},
        "physio": asdict(physio),
        "artifacts": asdict(art),
    }
    truth_path.write_text(
        json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, truth_path
