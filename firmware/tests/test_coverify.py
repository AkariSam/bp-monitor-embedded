"""S3：8 场景 PC 正本 ↔ 固件整链对拍。

两种口径：
- ``pc_on_recording``：PC 直接吃 CSV 的 12.5 ms 浮点网格（原始口径）；
- ``pc_on_buffer``：PC 吃固件 DeflateBuffer 的整数毫秒与 0.01 mmHg 压力
  （严格同输入口径）。

严格口径用于验证移植等价性；原始口径用于核对任务交付表的 PC 结果。
"""

import pytest

from coverify_util import (
    SCENARIOS,
    firmware_signal,
    load_scene,
    make_buffer,
    pc_on_buffer,
    pc_on_recording,
    pc_signal,
    run_firmware,
)


@pytest.mark.parametrize("name", SCENARIOS)
def test_pc_original_vs_firmware(name):
    rec, truth, _csv_path, _truth_path = load_scene(name)
    buffer = make_buffer(rec)
    pc = pc_signal(pc_on_recording(rec))
    result, _analyzer, _steps = run_firmware(buffer)
    fw = firmware_signal(result)

    assert fw["accepted"] == pc["accepted"]
    assert fw["qc_ok"] == pc["qc_ok"]
    assert fw["qc_reasons"] == pc["qc_reasons"]

    if pc["accepted"]:
        assert abs(fw["sbp"] - pc["sbp"]) <= 1.0
        assert abs(fw["dbp"] - pc["dbp"]) <= 1.0
        assert abs(fw["hr"] - pc["hr"]) <= 1.0
    else:
        assert fw["sbp"] is None

    if truth["scenario"] == "truncated":
        assert not fw["accepted"]
        assert "SBP 交叉未找到" in fw["qc_reasons"]


@pytest.mark.parametrize("name", SCENARIOS)
def test_pc_same_buffer_vs_firmware(name):
    """同一 t/p 输入下，移植应与 PC 正本数值等价。"""
    rec, _truth, _csv_path, _truth_path = load_scene(name)
    buffer = make_buffer(rec)
    pc = pc_signal(pc_on_buffer(buffer))
    result, _analyzer, _steps = run_firmware(buffer)
    fw = firmware_signal(result)

    assert fw["accepted"] == pc["accepted"]
    assert fw["qc_ok"] == pc["qc_ok"]
    assert fw["qc_reasons"] == pc["qc_reasons"]
    assert fw["n_candidates"] == pc["n_candidates"]
    assert fw["n_accepted"] == pc["n_accepted"]
    assert fw["n_points"] == pc["n_points"]

    if pc["accepted"]:
        # array('f') 工作区引入的额外误差；实测 <1e-4 mmHg。
        assert abs(fw["sbp"] - pc["sbp"]) <= 0.01
        assert abs(fw["dbp"] - pc["dbp"]) <= 0.01
        assert abs(fw["hr"] - pc["hr"]) <= 0.01
    else:
        assert fw["sbp"] is None


def _mae(signals, truths, key):
    errors = []
    for signal, truth in zip(signals, truths):
        if signal["accepted"]:
            errors.append(abs(signal[key] - truth[key]))
    assert errors
    return sum(errors) / len(errors)


def test_truth_mae_not_worse_than_pc_on_csv():
    pc_signals = []
    fw_signals = []
    truths = []
    for name in SCENARIOS:
        rec, truth, _csv_path, _truth_path = load_scene(name)
        buffer = make_buffer(rec)
        pc_signals.append(pc_signal(pc_on_recording(rec)))
        result, _analyzer, _steps = run_firmware(buffer)
        fw_signals.append(firmware_signal(result))
        truths.append(truth)

    pc_sbp = _mae(pc_signals, truths, "sbp")
    pc_dbp = _mae(pc_signals, truths, "dbp")
    fw_sbp = _mae(fw_signals, truths, "sbp")
    fw_dbp = _mae(fw_signals, truths, "dbp")

    assert fw_sbp <= pc_sbp + 0.05
    assert fw_dbp <= pc_dbp + 0.05
    assert fw_sbp <= 1.0
    assert fw_dbp <= 1.0
