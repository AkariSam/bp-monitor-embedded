"""sim/generate.py 单元测试：文件产出、契约、复现性、采样保真度。"""

import json

import numpy as np

from bp.csvio import PHASES, read_csv, validate
from sim.generate import generate, sample_grid
from sim.model import FS_OUT, FS_SIM, PhysioParams, synthesize


def test_generate_creates_contract_files(tmp_path):
    csv_path, truth_path = generate("normal", 1, tmp_path)
    assert csv_path.exists() and truth_path.exists()

    rec = read_csv(csv_path)
    assert validate(rec) == []
    assert rec.t_ms[0] == 0.0
    assert set(np.unique(rec.phase)) <= set(PHASES)

    seen = []
    for ph in rec.phase:
        if not seen or seen[-1] != ph:
            seen.append(ph)
    assert seen == ["INFLATE", "SETTLE", "DEFLATE", "DUMP"]

    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    for key in ("scenario", "seed", "sbp", "dbp", "hr", "map", "shape_resolved",
                "physio", "artifacts"):
        assert key in truth
    assert truth["sbp"] == 120.0
    assert abs(truth["map"] - 96.4) < 0.5   # k=0.41 锚点：0.41·120 + 0.59·80
    assert set(truth["shape_resolved"]) == {"sigma_n", "sigma_p", "mu"}


def test_generate_reproducible_bytes(tmp_path):
    csv1, truth1 = generate("normal", 3, tmp_path / "a")
    csv2, truth2 = generate("normal", 3, tmp_path / "b")
    assert csv1.read_bytes() == csv2.read_bytes()
    assert truth1.read_bytes() == truth2.read_bytes()


def test_generate_different_seeds_differ(tmp_path):
    a = read_csv(generate("normal", 1, tmp_path / "a")[0]).p_raw
    b = read_csv(generate("normal", 2, tmp_path / "b")[0]).p_raw
    assert not np.array_equal(a, b)


def test_generate_fs40_smoke(tmp_path):
    csv_path, _ = generate("normal", 1, tmp_path, fs_out=40.0)
    rec = read_csv(csv_path)
    assert validate(rec, dt_ms=25.0) == []


def test_sample_grid_mapping():
    sig = synthesize(PhysioParams(), rng=np.random.default_rng(0))
    t_ms, idx = sample_grid(sig.t, FS_SIM, FS_OUT)
    assert t_ms[1] - t_ms[0] == 12.5
    assert np.all(idx >= 0) and np.all(idx < sig.t.size)
    err = np.abs(sig.t[idx] * 1000.0 - t_ms)
    assert err.max() <= 0.5 + 1e-9


def test_sampling_fidelity_beat_amplitude():
    """80 Hz 采样的逐搏振幅相对 1 kHz 参考误差 ≤5%。"""
    sig = synthesize(PhysioParams(), rng=np.random.default_rng(0))
    t_ms, idx = sample_grid(sig.t, FS_SIM, FS_OUT)
    t_out = t_ms / 1000.0
    p_out = sig.p_clean[idx]

    beats = sig.beats
    checked = 0
    for i in range(11, len(beats) - 10):
        tb, tn = beats[i], beats[i + 1]
        w0, w1 = tb + 0.05 * (tn - tb), tb + 0.95 * (tn - tb)
        dm = (sig.t >= w0) & (sig.t <= w1)
        sm = (t_out >= w0) & (t_out <= w1)
        amp_dense = np.ptp(sig.p_clean[dm])
        amp_sampled = np.ptp(p_out[sm])
        if amp_dense > 0.3:
            rel = abs(amp_sampled - amp_dense) / amp_dense
            assert rel <= 0.05, f"beat {i}: dense={amp_dense:.3f} sampled={amp_sampled:.3f}"
            checked += 1
    assert checked >= 20
