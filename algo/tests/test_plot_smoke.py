"""scripts/plot_data.py 与 plot_spectrum.py 冒烟测试：CLI 产出非空 PNG。"""

import subprocess
import sys
from pathlib import Path

ALGO = Path(__file__).resolve().parents[1]
PLOT = ALGO / "scripts" / "plot_data.py"
SPECTRUM = ALGO / "scripts" / "plot_spectrum.py"


def test_plot_cli_smoke(tmp_path):
    r = subprocess.run(
        [sys.executable, str(PLOT), "--scenario", "normal", "--seed", "1",
         "--data-dir", str(tmp_path / "data"), "--out", str(tmp_path / "figs")],
        cwd=ALGO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    png = tmp_path / "figs" / "normal_s001.png"
    assert png.exists() and png.stat().st_size > 10_000


def test_plot_cli_existing_csv(tmp_path):
    data_dir = tmp_path / "data"
    figs_dir = tmp_path / "figs"
    r1 = subprocess.run(
        [sys.executable, str(PLOT), "--scenario", "af", "--seed", "1",
         "--data-dir", str(data_dir), "--out", str(figs_dir)],
        cwd=ALGO, capture_output=True, text=True)
    assert r1.returncode == 0, r1.stderr
    csv_path = data_dir / "af_s001.csv"
    r2 = subprocess.run(
        [sys.executable, str(PLOT), "--csv", str(csv_path), "--out", str(figs_dir)],
        cwd=ALGO, capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    assert (figs_dir / "af_s001.png").exists()


def test_spectrum_cli_smoke(tmp_path):
    r = subprocess.run(
        [sys.executable, str(SPECTRUM), "--scenario", "normal", "--seed", "1",
         "--data-dir", str(tmp_path / "data"), "--out", str(tmp_path / "figs")],
        cwd=ALGO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    png = tmp_path / "figs" / "normal_s001_spec.png"
    assert png.exists() and png.stat().st_size > 10_000
    assert "HR_est" in r.stdout
