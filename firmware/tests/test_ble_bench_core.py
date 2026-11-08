"""Phase 2A 合成测量序列测试。"""

import config
import ble_tx
from ble_bench_core import SyntheticBench


def advance(core, ms, step=100):
    frames = []
    left = ms
    while left > 0:
        dt = min(step, left)
        core.tick(dt)
        frame = core.take_frame()
        if frame is not None:
            frames.append(frame)
        left -= dt
    return frames


def advance_to_done(core):
    core.start()
    core.take_frame()
    return advance(core, 13000)


def test_start_runs_full_ok_sequence():
    core = SyntheticBench()
    assert core.start() is True
    assert core.take_frame()["state"] == config.STATE_SELF_CHECK

    frames = advance(core, 13000)
    states = [f["state"] for f in frames]
    assert states == [
        config.STATE_INFLATE,
        config.STATE_SETTLE,
        config.STATE_DEFLATE,
        config.STATE_ANALYZE,
        config.STATE_DONE,
    ]

    done = frames[-1]
    assert done["result"] == config.RESULT_OK
    assert done["reason"] is None
    assert done["data"] == {"sbp": 120.0, "dbp": 80.0, "map": 93.0, "hr": 72}


def test_scenarios_cycle_ok_reject_fault():
    core = SyntheticBench()

    assert advance_to_done(core)[-1]["result"] == config.RESULT_OK

    frames = advance_to_done(core)
    assert frames[-1]["result"] == config.RESULT_REJECT
    assert frames[-1]["data"] is None
    assert frames[-1]["reason"] == config.REASON_ANALYZER_REJECT

    frames = advance_to_done(core)
    assert frames[-1]["result"] == config.RESULT_FAULT
    assert frames[-1]["data"] is None
    assert frames[-1]["reason"] == config.REASON_OVERPRESSURE_260

    frames = advance_to_done(core)
    assert frames[-1]["result"] == config.RESULT_OK


def test_stop_goes_dump_then_idle_without_result():
    core = SyntheticBench()
    core.start()
    core.take_frame()
    advance(core, 2000)

    assert core.stop() is True
    dump = core.take_frame()
    assert dump["state"] == config.STATE_DUMP
    assert dump["result"] is None

    frames = advance(core, 2000)
    assert frames[-1]["state"] == config.STATE_IDLE
    assert frames[-1]["result"] is None


def test_pressure_profile_and_disabled_states():
    core = SyntheticBench()
    core.start()
    frame = core.take_frame()
    assert frame["p_mmHg"] == 0.0

    advance(core, 1000)
    assert core.state == config.STATE_INFLATE
    assert core.pressure == 0.0

    advance(core, 2500)
    assert abs(core.pressure - 90.0) < 1e-9

    advance(core, 2500)
    assert core.state == config.STATE_SETTLE
    assert core.pressure == 180.0

    advance(core, 1000)
    assert core.state == config.STATE_DEFLATE
    assert core.pressure == 180.0

    advance(core, 2500)
    assert abs(core.pressure - 110.0) < 1e-9

    advance(core, 2500)
    assert core.state == config.STATE_ANALYZE
    assert core.pressure is None
    assert core.pressure_enabled is False

    advance(core, 1000)
    assert core.state == config.STATE_DONE
    assert core.pressure is None


def test_start_ignored_while_measuring_and_stop_ignored_when_done():
    core = SyntheticBench()
    core.start()
    assert core.start() is False

    advance(core, 13000)
    assert core.state == config.STATE_DONE
    assert core.stop() is False


def test_done_frame_has_result_and_tx_order():
    core = SyntheticBench()
    frames = advance_to_done(core)
    done = frames[-1]

    assert done["state"] == config.STATE_DONE
    assert done["data"] is not None

    events = ble_tx.tx_events(done)
    assert len(events) == 2
    assert events[0][0] == "result"
    assert len(events[0][1]) == 9
    assert events[1] == ("status", b"\x04")


def test_reject_frame_has_no_result_data_and_tx_error():
    core = SyntheticBench()
    advance_to_done(core)  # OK
    frames = advance_to_done(core)  # REJECT
    done = frames[-1]

    assert done["data"] is None
    assert ble_tx.tx_events(done) == [
        ("error", b"\x03"),
        ("status", b"\x05"),
    ]


def test_current_frame_has_no_result_before_done():
    core = SyntheticBench()
    frame = core.current_frame()
    assert frame["state"] == config.STATE_IDLE
    assert frame["result"] is None
    assert frame["data"] is None
