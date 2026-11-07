"""自检失败、充气超时、超压与总时长安全阈值。"""

import pytest

import config


def test_self_check_sensor_comm_failure(make_bench, analyzer):
    bench = make_bench(
        analyzer=analyzer, sensor_status=config.STATUS_COMM_FAIL
    )
    bench.fsm.start()

    # 压力未知时不能直接 DONE，必须先进入 DUMP。
    assert bench.run_until_state(config.STATE_DUMP, max_ms=5000)
    assert bench.fsm.result is None
    assert bench.driver.safe_state_calls >= 2
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False

    bench.driver.set_sensor_status(config.STATUS_OK)
    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_SENSOR_COMM_FAIL
    assert analyzer.calls == []


def test_self_check_residual_pressure_fails(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer, pressure_mmHg=30.0)
    bench.fsm.start()
    assert bench.run_until_done(max_ms=5000)

    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_SELF_CHECK_PRESSURE_HIGH
    assert bench.driver.safe_state_calls >= 2
    assert analyzer.calls == []


def test_self_check_not_ready_is_transient_then_times_out(make_bench, analyzer):
    bench = make_bench(
        analyzer=analyzer, sensor_status=config.STATUS_NOT_READY
    )
    bench.fsm.start()

    assert bench.run_until_state(config.STATE_DUMP, max_ms=5000)
    assert bench.fsm.result is None
    assert bench.driver.now_ms() >= bench.fsm.measurement_start_ms + config.SELF_CHECK_TIMEOUT_MS

    bench.driver.set_sensor_status(config.STATUS_OK)
    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_SELF_CHECK_TIMEOUT
    assert analyzer.calls == []


def test_self_check_valid_after_deadline_still_faults(make_bench, analyzer):
    bench = make_bench(
        analyzer=analyzer, sensor_status=config.STATUS_NOT_READY
    )
    bench.fsm.start()
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_SELF_CHECK

    bench.clock.advance(config.SELF_CHECK_TIMEOUT_MS + 100)
    bench.driver.set_sensor_status(config.STATUS_OK)
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_SELF_CHECK_TIMEOUT
    assert analyzer.calls == []


def test_inflate_timeout_faults_and_dumps(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer, pump_rate_mmhg_s=2.0)
    bench.fsm.start()
    assert bench.run_until_done(max_ms=45000)

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_INFLATE_TIMEOUT
    elapsed = bench.driver.now_ms() - bench.fsm.measurement_start_ms
    assert elapsed >= config.INFLATE_TIMEOUT_MS
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert bench.fsm.last_pressure < config.ANALYZE_PRESSURE_MMHG
    assert analyzer.calls == []


@pytest.mark.parametrize(
    "pressure, expected_reason",
    (
        (config.OVERPRESSURE_FAULT_MMHG, config.REASON_OVERPRESSURE_260),
        (config.OVERPRESSURE_LATCH_MMHG, config.REASON_OVERPRESSURE_LATCHED_280),
    ),
)
def test_overpressure_faults_and_dumps(
    make_bench, analyzer, pressure, expected_reason
):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)

    calls_before = bench.driver.safe_state_calls
    bench.driver.set_pressure(pressure)
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DUMP
    assert bench.driver.safe_state_calls >= calls_before + 1
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False

    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == expected_reason
    assert bench.fsm.last_pressure < config.ANALYZE_PRESSURE_MMHG
    assert analyzer.calls == []


def test_total_measurement_timeout(make_bench, analyzer):
    # 1 mmHg/s 慢放气：DEFLATE 无法在 120 s 前结束，但又不触发 10 s/5 mmHg 卡死。
    bench = make_bench(analyzer=analyzer, leak_rate_mmhg_s=1.0)
    bench.fsm.start()
    assert bench.run_until_done(max_ms=130000)

    elapsed = bench.driver.now_ms() - bench.fsm.measurement_start_ms
    assert elapsed > config.TOTAL_MEASUREMENT_TIMEOUT_MS
    assert elapsed < config.TOTAL_MEASUREMENT_TIMEOUT_MS + 5000
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_TOTAL_TIMEOUT
    assert bench.fsm.last_pressure < config.ANALYZE_PRESSURE_MMHG
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert analyzer.calls == []


def test_not_ready_samples_are_transient_and_recover(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)

    bench.driver.set_sensor_status(config.STATUS_NOT_READY)
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_INFLATE
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is True
    assert bench.driver.safe_state_calls == 1
    bench.clock.advance(100)

    for _ in range(2):
        bench.fsm.step()
        assert bench.fsm.state == config.STATE_INFLATE
        assert bench.driver.pump_on is False
        assert bench.driver.safe_state_calls == 1
        bench.clock.advance(100)

    assert bench.fsm.result is None
    bench.driver.set_sensor_status(config.STATUS_OK)
    bench.fsm.step()
    assert bench.driver.pump_on is True
    assert bench.fsm.state == config.STATE_INFLATE

    assert bench.run_until_done(max_ms=60000)
    assert bench.fsm.result == config.RESULT_OK
    assert len(analyzer.calls) == 1
