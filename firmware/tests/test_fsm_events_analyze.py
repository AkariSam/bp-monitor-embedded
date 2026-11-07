"""事件通道（poll_events / ble_poll）与 ANALYZE 分阶段会话。"""

import pytest

import config
from buffer import DeflateBuffer


class SessionAnalyzer(object):
    def __init__(self, steps_before_result=3, raise_on_step=False):
        self.steps_before_result = steps_before_result
        self.raise_on_step = raise_on_step
        self.start_calls = 0
        self.step_calls = 0
        self.buffer = None
        self.start_len = 0

    def start(self, buffer):
        self.start_calls += 1
        self.buffer = buffer
        self.start_len = len(buffer)

    def step(self):
        self.step_calls += 1
        if self.raise_on_step:
            raise RuntimeError("session failed")
        if self.step_calls >= self.steps_before_result:
            return (config.RESULT_OK, config.REASON_NONE, {"steps": self.step_calls})
        return None


@pytest.mark.parametrize(
    "start_event, stop_event",
    (
        (b"START", b"STOP"),
        ("START", "STOP"),
        ([b"START"], [b"STOP"]),
        (["START"], ["STOP"]),
    ),
)
def test_ble_poll_accepts_bytes_and_str_event_forms(
    make_bench, start_event, stop_event
):
    bench = make_bench()
    current = {"events": start_event}
    bench.driver.ble_poll = lambda: current["events"]

    bench.fsm.step()
    assert bench.fsm.state == config.STATE_SELF_CHECK

    current["events"] = stop_event
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_CANCEL
    assert bench.fsm.reason == config.REASON_USER_CANCEL


def test_event_container_accepts_generators_and_ignores_non_iterables(make_bench):
    bench = make_bench()

    def start_events():
        yield b"START"

    def stop_events():
        yield "STOP"

    bench.driver.ble_poll = lambda: start_events()
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_SELF_CHECK

    bench.driver.ble_poll = lambda: stop_events()
    bench.fsm.step()
    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_CANCEL

    non_iterable = make_bench()
    non_iterable.driver.ble_poll = lambda: 123
    non_iterable.fsm.step()
    assert non_iterable.fsm.state == config.STATE_IDLE

    dict_events = make_bench()
    dict_events.driver.ble_poll = lambda: {b"START": True}
    dict_events.fsm.step()
    assert dict_events.fsm.state == config.STATE_IDLE


def test_app_ble_stop_event_via_fake_queue(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_INFLATE)

    bench.driver.queue_ble_event(b"STOP")
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_CANCEL
    assert bench.fsm.reason == config.REASON_USER_CANCEL
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert analyzer.calls == []


def test_ble_confirm_event_returns_idle(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_done()

    bench.driver.queue_ble_event(["CONFIRM"])
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_IDLE
    assert bench.fsm.result is None
    assert bench.fsm.reason is None
    assert len(bench.fsm.buffer) == 0


def test_ble_poll_exception_is_counted_without_blocking(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)

    def broken_poll():
        raise RuntimeError("ble stack offline")

    bench.driver.ble_poll = broken_poll

    bench.fsm.start()
    assert bench.run_until_done()

    assert bench.fsm.result == config.RESULT_OK
    assert bench.fsm.ble_errors > 0
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert len(analyzer.calls) == 1


def test_session_analyzer_runs_to_ok(make_bench):
    session = SessionAnalyzer(steps_before_result=3)
    bench = make_bench(analyzer=session)
    bench.fsm.start()
    assert bench.run_until_done(max_ms=70000)

    assert bench.fsm.result == config.RESULT_OK
    assert bench.fsm.reason == config.REASON_NONE
    assert session.start_calls == 1
    assert session.step_calls == 3
    assert session.start_len > 100
    assert isinstance(session.buffer, DeflateBuffer)


def test_stop_between_analyze_stages(make_bench):
    session = SessionAnalyzer(steps_before_result=999)
    bench = make_bench(analyzer=session)
    bench.fsm.start()
    assert bench.run_until_state(config.STATE_ANALYZE)

    bench.fsm.step()
    assert bench.fsm.state == config.STATE_ANALYZE
    assert session.start_calls == 1
    assert session.step_calls == 0

    bench.fsm.step()
    assert bench.fsm.state == config.STATE_ANALYZE
    assert session.step_calls == 1

    bench.fsm.stop()
    bench.fsm.step()

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_CANCEL
    assert bench.fsm.reason == config.REASON_USER_CANCEL
    assert session.step_calls == 1


def test_session_analyzer_exception_becomes_reject(make_bench):
    session = SessionAnalyzer(steps_before_result=1, raise_on_step=True)
    bench = make_bench(analyzer=session)
    bench.fsm.start()
    assert bench.run_until_done(max_ms=70000)

    assert bench.fsm.result == config.RESULT_REJECT
    assert bench.fsm.reason == config.REASON_ANALYZER_ERROR
    assert session.start_calls == 1
    assert session.step_calls == 1
