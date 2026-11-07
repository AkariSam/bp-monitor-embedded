"""STOP 全局出口与放气卡死：泄压、不分析、结果承载。"""

import config


def _enter_deflate_with_samples(bench, min_samples=20):
    assert bench.run_until(
        lambda fsm: fsm.state == config.STATE_DEFLATE
        and len(fsm.buffer) >= min_samples
    )


def test_stop_during_deflate_dumps_without_analyzing(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    _enter_deflate_with_samples(bench)

    buffered = len(bench.fsm.buffer)
    calls_before = bench.driver.safe_state_calls
    bench.fsm.stop()
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DUMP
    assert bench.driver.safe_state_calls >= calls_before + 1
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert len(bench.fsm.buffer) == buffered
    assert analyzer.calls == []

    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.result == config.RESULT_CANCEL
    assert bench.fsm.reason == config.REASON_USER_CANCEL
    assert bench.fsm.last_pressure < config.ANALYZE_PRESSURE_MMHG
    assert analyzer.calls == []


def test_stop_in_dump_before_analyze(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_DUMP)
    assert bench.fsm.last_pressure >= config.ANALYZE_PRESSURE_MMHG

    bench.fsm.stop()
    bench.fsm.step()

    # 仍在快排段等待压力降到安全线以下，但已放弃分析。
    assert bench.fsm.state == config.STATE_DUMP
    assert analyzer.calls == []

    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.result == config.RESULT_CANCEL
    assert bench.fsm.reason == config.REASON_USER_CANCEL
    assert bench.fsm.last_pressure < config.ANALYZE_PRESSURE_MMHG
    assert analyzer.calls == []


def test_stop_in_settle_takes_global_exit(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_SETTLE)

    bench.fsm.stop()
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DUMP
    assert bench.driver.safe_state_calls >= 2
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False

    assert bench.run_until_done(max_ms=5000)
    assert bench.fsm.result == config.RESULT_CANCEL
    assert analyzer.calls == []


def test_deflate_stuck_faults_and_dumps(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer, leak_rate_mmhg_s=0.0)
    bench.fsm.start()
    assert bench.run_until_done(max_ms=25000)

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_DEFLATE_STUCK
    assert bench.driver.safe_state_calls >= 2
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert bench.fsm.last_pressure < config.ANALYZE_PRESSURE_MMHG
    assert analyzer.calls == []


def test_dump_timeout_faults_when_pressure_stuck(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)

    # 制造超压故障进入 DUMP，再让快排完全失效。
    bench.driver.set_dump_rate(0.0)
    bench.driver.set_pressure(config.OVERPRESSURE_FAULT_MMHG + 5.0)
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_DUMP
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    dump_entry_ms = bench.fsm.state_entered_ms

    assert bench.run_until_done(max_ms=config.DUMP_TIMEOUT_MS + 5000)
    assert bench.driver.now_ms() - dump_entry_ms >= config.DUMP_TIMEOUT_MS
    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_FAULT
    # DUMP_TIMEOUT 只作为事件记录，最终原因保留原始超压根因。
    assert bench.fsm.reason == config.REASON_OVERPRESSURE_260
    assert any(e["event"] == "DUMP_TIMEOUT" for e in bench.fsm.log)
    assert bench.fsm.last_pressure >= config.ANALYZE_PRESSURE_MMHG
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert analyzer.calls == []
    assert not any(e["event"] == "DUMP_DONE" for e in bench.fsm.log)


def test_normal_dump_completes_before_timeout(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_done()

    dump_entry_ms = None
    analyze_entry_ms = None
    for entry in bench.fsm.log:
        if entry["event"] == "DEFLATE_END":
            dump_entry_ms = entry["t_ms"]
        elif entry["event"] == "DUMP_DONE":
            analyze_entry_ms = entry["t_ms"]

    assert dump_entry_ms is not None
    assert analyze_entry_ms is not None
    assert analyze_entry_ms - dump_entry_ms < config.DUMP_TIMEOUT_MS
    assert bench.fsm.result == config.RESULT_OK
    assert bench.fsm.reason == config.REASON_NONE
    assert not any(e["event"] == "DUMP_TIMEOUT" for e in bench.fsm.log)
    assert len(analyzer.calls) == 1


def test_dump_timeout_without_prior_fault_uses_dump_timeout(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_DUMP)

    # 正常进入 DUMP 后快排失效，且没有其他故障根因。
    bench.driver.set_dump_rate(0.0)
    assert bench.run_until_done(max_ms=config.DUMP_TIMEOUT_MS + 5000)

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_DUMP_TIMEOUT
    assert bench.fsm.last_pressure >= config.ANALYZE_PRESSURE_MMHG
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert analyzer.calls == []
    assert any(e["event"] == "DUMP_TIMEOUT" for e in bench.fsm.log)


def test_dump_permanent_not_ready_hits_dump_timeout(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_DUMP)

    bench.driver.set_sensor_status(config.STATUS_NOT_READY)
    assert bench.run_until_done(max_ms=config.DUMP_TIMEOUT_MS + 5000)

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_SENSOR_NOT_READY_TIMEOUT
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert analyzer.calls == []
    assert any(e["event"] == "DUMP_TIMEOUT" for e in bench.fsm.log)
