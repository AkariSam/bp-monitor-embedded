"""冻结契约回归：状态位、8 状态、分析器注入与默认占位行为。"""

import config
from control_fsm import ControlFSM, default_analyze_deflate
from drivers.fake_driver import FakeDriver


def test_status_bits_are_frozen():
    assert config.STATUS_OK == 0
    assert config.STATUS_NOT_READY == 0x01
    assert config.STATUS_COMM_FAIL == 0x02
    assert config.STATUS_STALE == 0x04
    assert config.STATUS_SATURATED == 0x08
    assert config.STATUS_OUT_OF_RANGE == 0x10


def test_exactly_eight_states():
    assert len(config.ALL_STATES) == 8
    assert len(set(config.ALL_STATES)) == 8
    assert config.STATE_DONE in config.ALL_STATES
    assert config.STATE_ANALYZE in config.MEASUREMENT_STATES
    assert config.STATE_IDLE not in config.MEASUREMENT_STATES
    assert config.STATE_DONE not in config.MEASUREMENT_STATES


def test_default_analyzer_rejects_with_reason():
    assert default_analyze_deflate([]) == (
        config.RESULT_REJECT,
        config.REASON_ANALYZER_NOT_IMPLEMENTED,
        None,
    )


def test_analyze_deflate_alias_is_accepted(analyzer):
    driver = FakeDriver()
    fsm = ControlFSM(driver, analyze_deflate=analyzer)
    assert fsm.analyzer is analyzer


def test_both_analyzer_arguments_rejected(analyzer):
    driver = FakeDriver()
    try:
        ControlFSM(driver, analyzer=analyzer, analyze_deflate=analyzer)
    except ValueError:
        return
    raise AssertionError("同时提供 analyzer/analyze_deflate 应抛 ValueError")


def test_log_is_bounded(make_bench):
    bench = make_bench()
    for _ in range(config.MAX_LOG_ENTRIES + 20):
        bench.fsm._log("TEST")
    assert len(bench.fsm.log) == config.MAX_LOG_ENTRIES
