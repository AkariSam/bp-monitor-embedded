"""BLE GATT v1.0 编码/映射与 FSM→BLE 集成测试。"""

import config
import ble_protocol


def _hex(text):
    return bytes.fromhex(text)


# ---- 实时压力 ----

def test_pressure_fixed_vectors():
    assert ble_protocol.encode_pressure(0.0) == _hex("00 00")
    assert ble_protocol.encode_pressure(40.0) == _hex("90 01")
    assert ble_protocol.encode_pressure(123.4) == _hex("D2 04")
    assert ble_protocol.encode_pressure(180.0) == _hex("08 07")
    assert ble_protocol.encode_pressure(300.0) == _hex("B8 0B")


def test_pressure_invalid_is_not_sent():
    assert ble_protocol.encode_pressure(None) is None
    assert ble_protocol.encode_pressure(float("nan")) is None
    assert ble_protocol.encode_pressure(-0.1) is None
    assert ble_protocol.encode_pressure(300.1) is None
    assert ble_protocol.encode_pressure("bad") is None


# ---- SFLOAT ----

def test_sfloat_known_bytes():
    assert ble_protocol.encode_sfloat(120, 0) == _hex("78 00")
    assert ble_protocol.encode_sfloat(123.4, -1) == _hex("D2 F4")
    assert ble_protocol.encode_sfloat(-10, 0) == _hex("F6 0F")
    assert ble_protocol.encode_sfloat(2048, 0) is None


def test_sfloat_round_trip():
    for value, exponent in ((120.0, 0), (123.4, -1), (-10.0, 0), (0.0, 0)):
        raw = ble_protocol.encode_sfloat(value, exponent)
        assert raw is not None
        decoded = ble_protocol.decode_sfloat(raw)
        assert abs(decoded - value) < 1e-9


# ---- 0x2A35 结果 ----

def _good_result():
    return {"sbp": 120.0, "dbp": 80.0, "map": 93.0, "hr": 72}


def test_result_fixed_vector():
    assert ble_protocol.encode_result(_good_result()) == _hex(
        "04 78 00 50 00 5D 00 48 00"
    )


def test_result_decimal_uses_sfloat_negative_exponent():
    data = {"sbp": 120.3, "dbp": 80.2, "map": 93.3, "hr": 72}
    raw = ble_protocol.encode_result(data)
    assert raw is not None
    assert len(raw) == 9
    assert raw[0] == 0x04
    assert abs(ble_protocol.decode_sfloat(raw, 1) - 120.3) < 1e-9
    assert abs(ble_protocol.decode_sfloat(raw, 3) - 80.2) < 1e-9
    assert abs(ble_protocol.decode_sfloat(raw, 5) - 93.3) < 1e-9


def test_result_rejects_missing_or_invalid_fields():
    assert ble_protocol.encode_result(None) is None
    assert ble_protocol.encode_result({}) is None
    for key in ("sbp", "dbp", "map", "hr"):
        data = _good_result()
        data[key] = None
        assert ble_protocol.encode_result(data) is None
    for key in ("sbp", "dbp", "map"):
        data = _good_result()
        data[key] = 0.0
        assert ble_protocol.encode_result(data) is None
        data[key] = 301.0
        assert ble_protocol.encode_result(data) is None
    for hr in (0, 301, float("nan")):
        data = _good_result()
        data["hr"] = hr
        assert ble_protocol.encode_result(data) is None


# ---- 状态映射 ----

def test_state_codes():
    assert ble_protocol.state_code(config.STATE_IDLE) == 0
    assert ble_protocol.state_code(config.STATE_INFLATE) == 1
    assert ble_protocol.state_code(config.STATE_DEFLATE) == 2
    assert ble_protocol.state_code(config.STATE_ANALYZE) == 3
    assert ble_protocol.state_code(config.STATE_SELF_CHECK) == 6
    assert ble_protocol.state_code(config.STATE_SETTLE) == 7
    assert ble_protocol.state_code(config.STATE_DUMP) == 8
    assert ble_protocol.state_code(config.STATE_DONE, config.RESULT_OK) == 4
    assert ble_protocol.state_code(config.STATE_DONE, config.RESULT_REJECT) == 5
    assert ble_protocol.state_code(config.STATE_DONE, config.RESULT_FAULT) == 5
    assert ble_protocol.state_code(config.STATE_DONE, config.RESULT_CANCEL) == 0
    assert ble_protocol.state_code("UNKNOWN") is None


def test_status_bytes():
    assert ble_protocol.encode_status(config.STATE_IDLE) == b"\x00"
    assert ble_protocol.encode_status(config.STATE_DONE, config.RESULT_OK) == b"\x04"
    assert ble_protocol.encode_status(config.STATE_DONE, config.RESULT_CANCEL) == b"\x00"
    assert ble_protocol.encode_status("UNKNOWN") is None


# ---- 错误码映射 ----

def test_error_mapping_categories():
    assert ble_protocol.result_error_code(config.RESULT_REJECT) == 0x03
    assert ble_protocol.result_error_code(config.RESULT_OK) is None
    assert ble_protocol.result_error_code(config.RESULT_CANCEL) is None
    assert ble_protocol.result_error_code(
        config.RESULT_FAULT, config.REASON_SENSOR_COMM_FAIL
    ) == 0x01
    assert ble_protocol.result_error_code(
        config.RESULT_FAULT, config.REASON_OVERPRESSURE_260
    ) == 0x04
    assert ble_protocol.result_error_code(
        config.RESULT_FAULT, config.REASON_ACTUATOR_FAILED
    ) == 0x02
    assert ble_protocol.result_error_code(
        config.RESULT_FAULT, "BATTERY_LOW"
    ) == 0x05
    assert ble_protocol.result_error_code(
        config.RESULT_FAULT, "UNKNOWN_REASON"
    ) == 0x02
    assert ble_protocol.encode_error(config.RESULT_REJECT) == b"\x03"
    assert ble_protocol.encode_error(config.RESULT_OK) is None


# ---- 控制命令 ----

def test_control_decode():
    assert ble_protocol.decode_control(b"\x01") == "START"
    assert ble_protocol.decode_control(b"\x02") == "STOP"
    assert ble_protocol.decode_control([0x01]) == "START"
    assert ble_protocol.decode_control(b"\x03") is None
    assert ble_protocol.decode_control(b"") is None
    assert ble_protocol.decode_control(b"\x01\x02") is None
    assert ble_protocol.decode_control(None) is None


# ---- FSM 集成：DONE 帧必须带结果数据 ----

def _ok_analyzer(buffer):
    return (config.RESULT_OK, config.REASON_NONE, _good_result())


def test_done_ok_frame_contains_result_data(make_bench):
    bench = make_bench(analyzer=_ok_analyzer)
    bench.fsm.start()
    assert bench.run_until_done()

    frame = bench.driver.ble_frames[-1]
    assert frame["state"] == config.STATE_DONE
    assert frame["result"] == config.RESULT_OK
    assert frame["data"] == _good_result()
    assert ble_protocol.encode_result(frame["data"]) == _hex(
        "04 78 00 50 00 5D 00 48 00"
    )
    assert all(
        item["data"] is None
        for item in bench.driver.ble_frames
        if item["state"] != config.STATE_DONE
    )


def test_done_reject_frame_has_no_result_data(make_bench):
    def reject_analyzer(buffer):
        return (
            config.RESULT_REJECT,
            "SBP 交叉未找到",
            {"sbp": None, "dbp": None, "map": None, "hr": None},
        )

    bench = make_bench(analyzer=reject_analyzer)
    bench.fsm.start()
    assert bench.run_until_done()

    frame = bench.driver.ble_frames[-1]
    assert frame["state"] == config.STATE_DONE
    assert frame["result"] == config.RESULT_REJECT
    assert frame["data"] is None
    assert ble_protocol.encode_error(frame["result"], frame["reason"]) == b"\x03"
