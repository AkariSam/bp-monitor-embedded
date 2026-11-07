"""sweep.py 冒烟测试：导出锚点正确、迷你网格跑通、结构合法。"""

import json
from dataclasses import replace

from sim.generate import generate
from sim.model import PhysioParams

from scripts.sweep import envelope_markers, run_algo, run_ratio


def test_envelope_markers_anchored():
    """默认形状的解析包络锚点：r_s=0.50 / r_d=0.70 / k=0.41。"""
    m = envelope_markers(PhysioParams())
    assert abs(m["r_s"] - 0.50) < 0.01
    assert abs(m["r_d"] - 0.70) < 0.01
    assert abs(m["k"] - 0.41) < 0.01


def test_generate_override_truth(tmp_path):
    """physio/lsb 覆盖：真值 JSON 反映实际使用的参数。"""
    ph = replace(PhysioParams(), sbp=140.0, dbp=90.0)
    _, truth_path = generate("normal", 1, tmp_path, physio=ph, lsb=0.2)
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    assert truth["sbp"] == 140.0
    assert truth["dbp"] == 90.0


def test_algo_mini(tmp_path):
    res = run_algo(names=["normal"], seeds=[1],
                   grid={"grad_min": [0.25, 0.5]}, make_fig=False,
                   workdir=tmp_path)
    e = res["params"]["grad_min"]
    assert e["verdict"] in ("钉死", "观察", "脆弱")
    assert "0.25" in e["levels"]
    assert "0.5" in e["levels"]
    assert res["baseline"]["n_total"] == 1


def test_ratio_mini(tmp_path):
    res = run_ratio(names=["normal"], seeds=[1],
                    sbp_levels=[0.46, 0.48, 0.50, 0.52, 0.54],
                    dbp_levels=[0.66, 0.68, 0.70, 0.72, 0.74],
                    make_fig=False, workdir=tmp_path)
    # 零点锚定在管线常数处（±0.02）；换算率方向与文献一致
    assert abs(res["sbp"]["zero_crossing"] - 0.50) < 0.02
    assert res["sbp"]["slope_per_0.1"] < 0
    assert abs(res["dbp"]["zero_crossing"] - 0.70) < 0.02
    assert res["dbp"]["slope_per_0.1"] > 0
