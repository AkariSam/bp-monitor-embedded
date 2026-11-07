"""端到端集成测试：CLI 生成文件 → 契约校验 → 包络/放气速率 sanity。"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt

from bp.csvio import read_csv, validate

ALGO_ROOT = Path(__file__).resolve().parents[1]
GEN = ALGO_ROOT / "scripts" / "gen_data.py"


def _run_cli(args):
    return subprocess.run([sys.executable, str(GEN), *args],
                          cwd=ALGO_ROOT, capture_output=True, text=True)


def test_cli_list():
    r = _run_cli(["--list"])
    assert r.returncode == 0
    names = r.stdout.split()
    assert len(names) == 8 and "normal" in names


def test_cli_all_scenarios_pass_contract(tmp_path):
    r = _run_cli(["--all", "--seeds", "2", "--out", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    csvs = sorted(tmp_path.glob("*.csv"))
    assert len(csvs) == 8
    for csv_path in csvs:
        rec = read_csv(csv_path)
        assert validate(rec) == [], f"{csv_path.name} 契约校验未通过"
        truth_path = csv_path.with_suffix(".truth.json")
        assert truth_path.exists()


def test_cli_normal_end_to_end(tmp_path):
    r = _run_cli(["--scenario", "normal", "--seed", "1",
                  "--out", str(tmp_path)])
    assert r.returncode == 0, r.stderr
    csv_path = tmp_path / "normal_s001.csv"
    truth_path = tmp_path / "normal_s001.truth.json"
    assert csv_path.exists() and truth_path.exists()

    rec = read_csv(csv_path)
    assert validate(rec) == []

    # 仅用 DEFLATE 段做 sanity（管线正式分析区）
    m = rec.phase == "DEFLATE"
    t = rec.t_ms[m] / 1000.0
    p = rec.p_raw[m]

    sos_bp = butter(4, [0.5, 8.0], btype="bandpass", fs=80.0, output="sos")
    ac = sosfiltfilt(sos_bp, p)
    sos_dc = butter(2, 0.5, btype="lowpass", fs=80.0, output="sos")
    dc = sosfiltfilt(sos_dc, p)

    # 包络峰值位置 ≈ 真值 MAP（±8 mmHg）
    win, hop = 80, 20  # 1 s 窗
    env, env_pc = [], []
    for k in range(0, len(ac) - win, hop):
        env.append(np.ptp(ac[k:k + win]))
        env_pc.append(dc[k:k + win].mean())
    map_est = env_pc[int(np.argmax(env))]

    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    assert abs(map_est - truth["map"]) <= 8.0

    # 放气速率反估 ∈ [2, 5] mmHg/s
    rate = -np.polyfit(t, p, 1)[0]
    assert 2.0 <= rate <= 5.0
