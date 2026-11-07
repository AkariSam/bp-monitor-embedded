"""正常测量路径、DONE 结果承载与显示/通信降级。"""

import config


def test_normal_flow_calls_analyzer_and_returns_ok(make_bench, analyzer):
    analyzer.as_string = True  # 假分析器直接返回 "OK"
    bench = make_bench(analyzer=analyzer)

    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)
    assert bench.driver.valve_energized is True
    assert bench.driver.pump_on is True

    assert bench.run_until_done()

    fsm = bench.fsm
    driver = bench.driver
    assert fsm.state == config.STATE_DONE
    assert fsm.result == config.RESULT_OK
    assert fsm.reason == config.REASON_NONE
    assert fsm.result_data is None

    assert len(analyzer.calls) == 1
    buf = analyzer.calls[0]
    assert len(buf) > 100
    assert len(buf) == len(fsm.buffer)

    seqs = [s[0] for s in buf]
    times = [s[1] for s in buf]
    pressures = [s[2] for s in buf]
    assert seqs == sorted(seqs)
    assert seqs[0] < seqs[-1]
    assert times == sorted(times)
    assert times[0] < times[-1]
    assert pressures[0] > pressures[-1]
    assert max(pressures) <= config.TARGET_PRESSURE_MMHG + 5.0
    assert pressures[-1] < config.DEFLATE_END_PRESSURE_MMHG

    assert driver.pump_on is False
    assert driver.valve_energized is False
    assert driver.safe_state_calls >= 1
    assert driver.pressure_read_calls > 100
    assert fsm.last_pressure is not None
    assert fsm.last_pressure < config.ANALYZE_PRESSURE_MMHG

    required = ("t_ms", "p_mmHg", "state", "pump", "valve", "event")
    assert fsm.log
    for entry in fsm.log:
        for key in required:
            assert key in entry

    # DONE 后再次 START 进入新一轮自检，缓冲与旧结果清空。
    bench.fsm.start()
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_SELF_CHECK
    assert bench.fsm.result is None
    assert bench.fsm.reason is None
    assert len(bench.fsm.buffer) == 0


def test_default_analyzer_is_placeholder_reject(make_bench):
    bench = make_bench()
    bench.fsm.start()
    assert bench.run_until_done()

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_REJECT
    assert bench.fsm.reason == config.REASON_ANALYZER_NOT_IMPLEMENTED
    assert len(bench.fsm.buffer) > 100
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False


def test_analyzer_reject_reaches_done_reject_and_confirm_returns_idle(make_bench):
    calls = []

    def reject_analyzer(buffer):
        calls.append(len(buffer))
        return (config.RESULT_REJECT, "LOW_SNR", {"n": len(buffer)})

    bench = make_bench(analyzer=reject_analyzer)
    bench.fsm.start()
    assert bench.run_until_done()

    assert len(calls) == 1
    assert bench.fsm.result == config.RESULT_REJECT
    assert bench.fsm.reason == "LOW_SNR"
    assert bench.fsm.result_data == {"n": calls[0]}

    bench.fsm.confirm()
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_IDLE
    assert bench.fsm.result is None
    assert bench.fsm.reason is None
    assert len(bench.fsm.buffer) == 0


def test_analyzer_exception_becomes_reject_not_crash(make_bench):
    def broken_analyzer(buffer):
        raise RuntimeError("algorithm exploded")

    bench = make_bench(analyzer=broken_analyzer)
    bench.fsm.start()
    assert bench.run_until_done()

    assert bench.fsm.result == config.RESULT_REJECT
    assert bench.fsm.reason == config.REASON_ANALYZER_ERROR


def test_display_and_ble_exceptions_do_not_block_measurement(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.driver.fail_display = True
    bench.driver.fail_ble = True

    bench.fsm.start()
    assert bench.run_until_done()

    assert bench.fsm.result == config.RESULT_OK
    assert len(analyzer.calls) == 1
    assert bench.fsm.display_errors > 0
    assert bench.fsm.ble_errors > 0
    assert bench.driver.pressure_read_calls > 100
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False


def test_polled_start_and_stop_events(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)

    bench.driver.queue_event("START")
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_SELF_CHECK

    bench.driver.queue_event("STOP")
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_CANCEL
    assert bench.fsm.reason == config.REASON_USER_CANCEL
    assert analyzer.calls == []
