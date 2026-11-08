"""把控制层状态帧转换成 GATT 发送事件（纯逻辑，可在 PC 上测试）。

`ble_server` 只负责把这里产生的事件写到对应的 BLE 特征；不在这里做 BLE 操作。

事件格式：
    ("result", bytes)   0x2A35 Indicate
    ("status", bytes)   状态 Notify
    ("error", bytes)    错误码 Notify
"""

import ble_protocol
import config


def tx_events(frame):
    """返回一个 frame 对应的有序事件列表。

    顺序遵循 GATT v1.0：
    - DONE(OK)：结果 -> completed(4)；
    - DONE(REJECT/FAULT)：错误码 -> error(5)；
    - DONE(CANCEL)：idle(0)（DUMP 状态通常会先单独发布）；
    - 其他状态：状态码。
    """
    if not isinstance(frame, dict):
        return []

    state = frame.get("state")
    result = frame.get("result")

    if state != config.STATE_DONE:
        status = ble_protocol.encode_status(state, result)
        if status is None:
            return []
        return [("status", status)]

    if result == config.RESULT_OK:
        payload = ble_protocol.encode_result(frame.get("data"))
        if payload is None:
            # DONE(OK) 但结果字段不完整：降级为测量判废，绝不发假结果。
            return [
                ("error", bytes((ble_protocol.ERROR_QUALITY,))),
                ("status", ble_protocol.encode_status(
                    config.STATE_DONE, config.RESULT_REJECT)),
            ]
        return [
            ("result", payload),
            ("status", ble_protocol.encode_status(
                config.STATE_DONE, config.RESULT_OK)),
        ]

    if result in (config.RESULT_REJECT, config.RESULT_FAULT):
        error = ble_protocol.encode_error(result, frame.get("reason"))
        if error is None:
            error = bytes((ble_protocol.ERROR_DEVICE,))
        status = ble_protocol.encode_status(state, result)
        return [("error", error), ("status", status)]

    if result == config.RESULT_CANCEL:
        status = ble_protocol.encode_status(state, result)
        return [("status", status)] if status is not None else []

    return []


def status_event(frame):
    """只取当前状态码，不发结果和错误。

    用于重连后的“补发当前状态”，避免把已经错过的结果补发一次。
    """
    if not isinstance(frame, dict):
        return None
    payload = ble_protocol.encode_status(
        frame.get("state"), frame.get("result")
    )
    if payload is None:
        return None
    return ("status", payload)


def pressure_event(p_mmHg):
    """压力事件；无效值返回 None，调用方不发送。"""
    payload = ble_protocol.encode_pressure(p_mmHg)
    if payload is None:
        return None
    return ("pressure", payload)
