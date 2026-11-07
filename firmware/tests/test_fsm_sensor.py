"""传感器安全状态位与采样异常。"""

import pytest

import config


@pytest.mark.parametrize(
    "status, expected_reason",
    (
        (config.STATUS_COMM_FAIL, config.REASON_SENSOR_COMM_FAIL),
        (config.STATUS_STALE, config.REASON_SENSOR_STALE),
        (config.STATUS_SATURATED, config.REASON_SENSOR_SATURATED),
        (config.STATUS_OUT_OF_RANGE, config.REASON_SENSOR_OUT_OF_RANGE),
    ),
)
def test_sensor_status_is_safety_fault(make_bench, analyzer, status, expected_reason):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)

    calls_before = bench.driver.safe_state_calls
    bench.driver.set_sensor_status(status)
    bench.fsm.step()

    assert bench.driver.safe_state_calls >= calls_before + 1
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == expected_reason
    assert analyzer.calls == []


def test_sensor_fault_during_deflate_dumps_then_faults(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until(
        lambda fsm: fsm.state == config.STATE_DEFLATE
        and len(fsm.buffer) >= 20
    )

    calls_before = bench.driver.safe_state_calls
    bench.driver.set_sensor_status(config.STATUS_COMM_FAIL)
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DUMP
    assert bench.driver.safe_state_calls >= calls_before + 1
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False

    # 传感器恢复后快排到 <15 mmHg，但结果保持 FAULT 且不分析。
    bench.driver.set_sensor_status(config.STATUS_OK)
    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_SENSOR_COMM_FAIL
    assert bench.fsm.last_pressure < config.ANALYZE_PRESSURE_MMHG
    assert analyzer.calls == []


def test_pressure_read_exception_is_comm_failure(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)

    bench.driver.raise_on_pressure_read = True
    bench.fsm.step()

    assert bench.driver.safe_state_calls >= 2
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_SENSOR_COMM_FAIL
    assert analyzer.calls == []


def test_seq_regression_during_deflate_faults_without_analyzing(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until(
        lambda fsm: fsm.state == config.STATE_DEFLATE
        and len(fsm.buffer) >= 20
    )
    buffered = len(bench.fsm.buffer)

    # 强制传感器 seq 倒退；该样本不得 append，直接走标准故障路径。
    bench.driver.reset_seq(0)
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DUMP
    assert len(bench.fsm.buffer) == buffered
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert any(e["event"] == "SEQ_REGRESSION" for e in bench.fsm.log)

    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_SENSOR_SEQ_REGRESSION
    assert analyzer.calls == []
    assert not any(e["event"] == "DUMP_DONE" for e in bench.fsm.log)
