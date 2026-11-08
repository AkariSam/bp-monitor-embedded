"""ble_tx 发送顺序测试（纯逻辑，不需要 BLE）。"""

import config
import ble_protocol
import ble_tx


def _hex(text):
    return bytes.fromhex(text)


def _frame(state, result=None, reason=None, data=None):
    return {
        "state": state,
        "result": result,
        "reason": reason,
        "data": data,
    }


def test_ok_sends_result_then_completed():
    frame = _frame(
        config.STATE_DONE,
        config.RESULT_OK,
        data={"sbp": 120.0, "dbp": 80.0, "map": 93.0, "hr": 72},
    )
    assert ble_tx.tx_events(frame) == [
        ("result", _hex("04 78 00 50 00 5D 00 48 00")),
        ("status", b"\x04"),
    ]


def test_reject_sends_error_then_error_status():
    frame = _frame(
        config.STATE_DONE,
        config.RESULT_REJECT,
        config.REASON_ANALYZER_REJECT,
    )
    assert ble_tx.tx_events(frame) == [
        ("error", b"\x03"),
        ("status", b"\x05"),
    ]


def test_fault_overpressure_sends_0x04_then_error_status():
    frame = _frame(
        config.STATE_DONE,
        config.RESULT_FAULT,
        config.REASON_OVERPRESSURE_260,
    )
    assert ble_tx.tx_events(frame) == [
        ("error", b"\x04"),
        ("status", b"\x05"),
    ]


def test_fault_sensor_connection_sends_0x01():
    frame = _frame(
        config.STATE_DONE,
        config.RESULT_FAULT,
        config.REASON_SENSOR_COMM_FAIL,
    )
    assert ble_tx.tx_events(frame) == [
        ("error", b"\x01"),
        ("status", b"\x05"),
    ]


def test_cancel_sends_idle_without_result_or_error():
    frame = _frame(config.STATE_DONE, config.RESULT_CANCEL)
    assert ble_tx.tx_events(frame) == [("status", b"\x00")]


def test_state_change_sends_single_status():
    assert ble_tx.tx_events(_frame(config.STATE_INFLATE)) == [
        ("status", b"\x01")
    ]
    assert ble_tx.tx_events(_frame(config.STATE_IDLE)) == [
        ("status", b"\x00")
    ]


def test_ok_without_hr_degrades_to_quality_error():
    frame = _frame(
        config.STATE_DONE,
        config.RESULT_OK,
        data={"sbp": 120.0, "dbp": 80.0, "map": 93.0, "hr": None},
    )
    assert ble_tx.tx_events(frame) == [
        ("error", b"\x03"),
        ("status", b"\x05"),
    ]


def test_unknown_done_result_sends_nothing():
    assert ble_tx.tx_events(_frame(config.STATE_DONE, None)) == []


def test_status_event_does_not_replay_done_result():
    frame = _frame(
        config.STATE_DONE,
        config.RESULT_OK,
        data={"sbp": 120.0, "dbp": 80.0, "map": 93.0, "hr": 72},
    )
    assert ble_tx.status_event(frame) == ("status", b"\x04")
    assert ble_tx.status_event(_frame(config.STATE_DONE, config.RESULT_REJECT)) == (
        "status",
        b"\x05",
    )
    assert ble_tx.status_event(_frame(config.STATE_INFLATE)) == (
        "status",
        b"\x01",
    )


def test_pressure_event():
    assert ble_tx.pressure_event(123.4) == ("pressure", _hex("D2 04"))
    assert ble_tx.pressure_event(None) is None
    assert ble_tx.pressure_event(300.1) is None


def test_protocol_result_length_is_nine_bytes():
    payload = ble_protocol.encode_result(
        {"sbp": 120.0, "dbp": 80.0, "map": 93.0, "hr": 72}
    )
    assert payload is not None
    assert len(payload) == 9
