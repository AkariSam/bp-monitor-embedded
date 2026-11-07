"""新增安全边界：NOT_READY、未知/过期压力、STOP 优先级、安全原语重试。"""

import pytest

import config


def test_not_ready_in_inflate_stops_pump_and_faults(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)
    assert bench.driver.pump_on is True

    bench.driver.set_sensor_status(config.STATUS_NOT_READY)
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_INFLATE
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is True
    bench.clock.advance(100)

    for _ in range(6):
        bench.fsm.step()
        if bench.fsm.state != config.STATE_INFLATE:
            break
        bench.clock.advance(100)

    assert bench.fsm.state == config.STATE_DUMP
    assert bench.fsm.result is None

    bench.driver.set_sensor_status(config.STATUS_OK)
    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_SENSOR_NOT_READY_TIMEOUT
    assert analyzer.calls == []


@pytest.mark.parametrize(
    "state",
    (config.STATE_SETTLE, config.STATE_DEFLATE, config.STATE_DUMP),
)
def test_not_ready_timeout_in_measurement_states(make_bench, analyzer, state):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(state)

    bench.driver.set_sensor_status(config.STATUS_NOT_READY)
    for _ in range(10):
        bench.fsm.step()
        if bench.fsm.state != state:
            break
        bench.clock.advance(100)

    assert bench.fsm.state == config.STATE_DUMP
    assert bench.fsm.result is None

    bench.driver.set_sensor_status(config.STATUS_OK)
    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_SENSOR_NOT_READY_TIMEOUT
    assert analyzer.calls == []


def test_stop_with_unknown_pressure_enters_dump(make_bench, analyzer):
    bench = make_bench(
        analyzer=analyzer, sensor_status=config.STATUS_NOT_READY
    )
    bench.fsm.start()
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_SELF_CHECK

    bench.fsm.stop()
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DUMP
    assert bench.fsm.result is None
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False

    bench.driver.set_sensor_status(config.STATUS_OK)
    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.result == config.RESULT_CANCEL
    assert bench.fsm.reason == config.REASON_USER_CANCEL
    assert analyzer.calls == []


def test_stop_with_stale_pressure_enters_dump(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)

    bench.driver.set_sensor_status(config.STATUS_NOT_READY)
    bench.clock.advance(config.SENSOR_NOT_READY_TIMEOUT_MS + 100)
    bench.fsm.stop()
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DUMP
    assert bench.fsm.result is None

    bench.driver.set_sensor_status(config.STATUS_OK)
    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.result == config.RESULT_CANCEL
    assert bench.fsm.reason == config.REASON_USER_CANCEL
    assert analyzer.calls == []


@pytest.mark.parametrize(
    "pressure, expected_reason",
    (
        (config.OVERPRESSURE_FAULT_MMHG + 5.0, config.REASON_OVERPRESSURE_260),
        (config.OVERPRESSURE_LATCH_MMHG + 5.0, config.REASON_OVERPRESSURE_LATCHED_280),
    ),
)
def test_stop_overpressure_fault_wins_over_cancel(
    make_bench, analyzer, pressure, expected_reason
):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)

    bench.driver.set_pressure(pressure)
    bench.fsm.stop()
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DUMP
    assert bench.fsm.result is None
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False

    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == expected_reason
    assert analyzer.calls == []


def test_pressure_below_min_is_out_of_range(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)

    bench.driver.set_pressure(config.MIN_VALID_PRESSURE_MMHG - 1.0)
    bench.fsm.step()

    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_SENSOR_OUT_OF_RANGE
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert analyzer.calls == []


def test_safe_state_transient_failure_is_retried(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)

    # 复合调用失败，且单路回退中的泵通道也失败，形成待重试状态。
    bench.driver.fail_safe_state = True
    bench.driver.fail_pump = True
    bench.fsm.stop()
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DUMP
    assert bench.fsm.safe_state_errors >= 1
    assert bench.fsm.actuator_errors >= 1
    assert bench.fsm.is_healthy() is False
    assert bench.driver.pump_on is True
    assert bench.driver.valve_energized is False
    assert any(entry[1] and entry[2] for entry in bench.driver.safe_state_history)

    bench.fsm.step()
    assert bench.fsm.state == config.STATE_DUMP
    assert bench.fsm.safe_state_errors >= 2
    assert bench.fsm.is_healthy() is False
    assert (
        sum(1 for entry in bench.fsm.log if entry["event"] == "SAFE_STATE_ERROR")
        == 1
    )

    bench.driver.fail_safe_state = False
    bench.driver.fail_pump = False
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_SAFE_STATE_FAILED
    assert bench.fsm.is_healthy() is True
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False


def test_dump_timeout_cannot_finish_before_safe_state_succeeds(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_DUMP)

    # 模拟泵通道仍处于开启状态，使单路回退必须真正写 pump_set(False)。
    bench.driver.pump_on = True
    bench.fsm.pump_on = True
    bench.driver.set_sensor_status(config.STATUS_COMM_FAIL)
    bench.driver.fail_safe_state = True
    bench.driver.fail_pump = True
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_DUMP
    assert bench.fsm.is_healthy() is False
    assert (
        sum(1 for entry in bench.fsm.log if entry["event"] == "SAFE_STATE_ERROR")
        == 1
    )

    # 传感器恢复后仍不关闭 pending；此后不再反复产生传感器故障日志。
    bench.driver.set_sensor_status(config.STATUS_OK)
    for _ in range(310):
        bench.fsm.step()
        bench.clock.advance(100)

    # 超过 DUMP_TIMEOUT 但 safe_state 一直失败，禁止进入 DONE。
    assert bench.fsm.state == config.STATE_DUMP
    assert bench.fsm.result is None
    assert bench.fsm.safe_state_errors > 0
    assert bench.fsm.is_healthy() is False
    assert (
        sum(1 for entry in bench.fsm.log if entry["event"] == "SAFE_STATE_ERROR")
        == 1
    )
    assert (
        sum(
            1
            for entry in bench.fsm.log
            if entry["event"] == "DUMP_TIMEOUT_SAFE_STATE_PENDING"
        )
        == 1
    )

    bench.driver.fail_safe_state = False
    bench.driver.fail_pump = False
    assert bench.run_until_done(max_ms=5000)

    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.is_healthy() is True
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False


def test_safe_state_fallback_closes_channels(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)
    for _ in range(10):
        bench.fsm.step()
        bench.clock.advance(100)

    p_before = bench.driver.pressure_mmHg
    bench.driver.fail_safe_state = True
    bench.fsm.stop()
    bench.fsm.step()

    # 复合 safe_state 失败，但单路 pump/valve 写入均成功。
    assert bench.fsm.is_healthy() is True
    assert bench.fsm.safe_state_errors >= 1
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert any(
        entry["event"] == "SAFE_STATE_FALLBACK_OK" for entry in bench.fsm.log
    )

    for _ in range(5):
        if bench.fsm.state == config.STATE_DONE:
            break
        bench.fsm.step()
        bench.clock.advance(100)
    assert bench.driver.pressure_mmHg <= p_before


def test_safe_state_fallback_skips_already_off_channels(make_bench):
    bench = make_bench()
    bench.driver.fail_safe_state = True
    bench.driver.fail_pump = True
    bench.driver.fail_valve = True
    pump_calls_before = bench.driver.pump_set_calls
    valve_calls_before = bench.driver.valve_set_calls

    bench.fsm.stop()
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_IDLE
    assert bench.fsm.is_healthy() is True
    assert bench.fsm.safe_state_errors >= 1
    assert bench.fsm.actuator_errors == 0
    assert bench.driver.pump_set_calls == pump_calls_before
    assert bench.driver.valve_set_calls == valve_calls_before


def test_safe_state_pending_retries_even_in_idle(make_bench):
    bench = make_bench()
    # 模拟泵通道仍开启，使单路回退真正需要写 pump_set(False) 并失败。
    bench.driver.pump_on = True
    bench.fsm.pump_on = True
    bench.driver.fail_safe_state = True
    bench.driver.fail_pump = True
    bench.fsm.stop()
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_IDLE
    assert bench.fsm.is_healthy() is False
    assert bench.fsm.safe_state_errors >= 1
    assert bench.driver.pump_on is True

    bench.driver.fail_safe_state = False
    bench.driver.fail_pump = False
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_IDLE
    assert bench.fsm.is_healthy() is True
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False


def test_safe_state_pending_retries_even_in_done(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_done()
    assert bench.fsm.state == config.STATE_DONE

    # 模拟泵通道仍开启，使 DONE 下的重试路径必须真正关闭它。
    bench.driver.pump_on = True
    bench.fsm.pump_on = True
    bench.driver.fail_safe_state = True
    bench.driver.fail_pump = True
    bench.fsm.stop()
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.is_healthy() is False
    assert bench.fsm.safe_state_errors >= 1
    assert bench.driver.pump_on is True

    bench.driver.fail_safe_state = False
    bench.driver.fail_pump = False
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.is_healthy() is True
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False


def test_stop_safe_action_precedes_pressure_read(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)

    bench.driver.operation_log = []
    bench.fsm.stop()
    bench.fsm.step()

    log = bench.driver.operation_log
    assert log[0] == "safe_state"
    assert "pressure_read" in log
    assert log.index("safe_state") < log.index("pressure_read")


@pytest.mark.parametrize("switch", ("fail_pump", "fail_valve"))
def test_actuator_write_failure_is_contained(make_bench, analyzer, switch):
    bench = make_bench(analyzer=analyzer)
    setattr(bench.driver, switch, True)

    bench.fsm.start()
    assert bench.run_until_done(max_ms=5000)

    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_ACTUATOR_FAILED
    assert bench.fsm.actuator_errors >= 1
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert analyzer.calls == []


def test_deflate_end_precedes_stall_window(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_SETTLE)

    # 在 SETTLE 末端把压力降到 40 附近，并让 DEFLATE 10 s 内压降 <5 mmHg。
    bench.driver.set_pressure(44.0)
    bench.driver.set_leak_rate(0.4)

    assert bench.run_until_done(max_ms=20000)
    assert bench.fsm.result == config.RESULT_OK
    assert bench.fsm.reason == config.REASON_NONE
    assert not any(
        entry["event"] == "FAULT_DEFLATE_STUCK" for entry in bench.fsm.log
    )
