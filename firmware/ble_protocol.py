"""BLE GATT v1.0 编码/解码（MicroPython 友好，无硬件依赖）。

正本：``docs/interfaces/ble-gatt-protocol.md`` v1.0。

本模块只做协议字节与状态/错误映射，不碰 GPIO、BLE 栈、控制状态机。
驱动层用这里的函数把 ``ControlFSM`` 的 frame 转成 GATT 特征值。
"""

import config


PROTOCOL_VERSION = "1.0"

# ---- GATT UUID ----
SERVICE_BLOOD_PRESSURE = "1810"
CHAR_BP_MEASUREMENT = "2a35"
SERVICE_CUSTOM = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
CHAR_PRESSURE = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
CHAR_STATUS = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
CHAR_ERROR = "6e400004-b5a3-f393-e0a9-e50e24dcca9e"
CHAR_CONTROL = "6e400005-b5a3-f393-e0a9-e50e24dcca9e"

# ---- 控制命令 ----
CONTROL_START = 0x01
CONTROL_STOP = 0x02
EVENT_START = "START"
EVENT_STOP = "STOP"

# ---- 错误码 ----
ERROR_CUFF = 0x01
ERROR_DEVICE = 0x02
ERROR_QUALITY = 0x03
ERROR_PRESSURE = 0x04
ERROR_BATTERY = 0x05

# ---- 量程 ----
MIN_PRESSURE_MMHG = 0.0
MAX_PRESSURE_MMHG = 300.0
MIN_HR_BPM = 1
MAX_HR_BPM = 300

# ---- 状态码映射（DONE 由 state_code() 单独处理） ----
_STATE_TO_CODE = {
    config.STATE_IDLE: 0,
    config.STATE_INFLATE: 1,
    config.STATE_DEFLATE: 2,
    config.STATE_ANALYZE: 3,
    config.STATE_SELF_CHECK: 6,
    config.STATE_SETTLE: 7,
    config.STATE_DUMP: 8,
}

# 只显式列出“用户能采取不同行动”的故障；其余内部/设备故障统一归 0x02。
# 不把 20 多个 FSM reason 一一映射到 APP，避免协议层承担内部诊断细节。
_FAULT_REASON_TO_ERROR = {
    config.REASON_SELF_CHECK_FAILED: ERROR_CUFF,
    config.REASON_SELF_CHECK_TIMEOUT: ERROR_CUFF,
    config.REASON_SENSOR_COMM_FAIL: ERROR_CUFF,
    config.REASON_SENSOR_NOT_READY_TIMEOUT: ERROR_CUFF,
    config.REASON_OVERPRESSURE_260: ERROR_PRESSURE,
    config.REASON_OVERPRESSURE_LATCHED_280: ERROR_PRESSURE,
    config.REASON_SELF_CHECK_PRESSURE_HIGH: ERROR_PRESSURE,
    "BATTERY_LOW": ERROR_BATTERY,
}


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_nan(value):
    return value != value


def encode_pressure(p_mmHg):
    """实时压力：2 字节 uint16 小端，单位 0.1 mmHg。

    返回 ``bytes``；无效/未就绪/超范围返回 ``None``（调用方不发送）。
    """
    p = _to_float(p_mmHg)
    if p is None or _is_nan(p):
        return None
    if p < MIN_PRESSURE_MMHG or p > MAX_PRESSURE_MMHG:
        return None
    raw = int(round(p * 10.0))
    if raw < 0 or raw > 3000:
        return None
    return bytes((raw & 0xFF, (raw >> 8) & 0xFF))


def encode_sfloat(value, exponent=0):
    """IEEE-11073 SFLOAT：4 位有符号指数 + 12 位有符号尾数。

    value = mantissa * 10**exponent。无效/溢出返回 ``None``。
    """
    v = _to_float(value)
    if v is None or _is_nan(v):
        return None
    if exponent < -8 or exponent > 7:
        return None
    scale = 10.0 ** exponent
    if scale == 0.0:
        return None
    mantissa = int(round(v / scale))
    if mantissa < -2048 or mantissa > 2047:
        return None
    word = ((exponent & 0x0F) << 12) | (mantissa & 0x0FFF)
    return bytes((word & 0xFF, (word >> 8) & 0xFF))


def encode_measurement_sfloat(value):
    """SBP/DBP/MAP：整数值用指数 0，非整数用指数 -1（0.1 mmHg）。"""
    v = _to_float(value)
    if v is None or _is_nan(v):
        return None
    rounded = int(round(v))
    if abs(v - rounded) < 1e-9:
        return encode_sfloat(rounded, 0)
    return encode_sfloat(v, -1)


def decode_sfloat(data, offset=0):
    """解码 SFLOAT，供回环测试/诊断使用。"""
    if data is None or len(data) < offset + 2:
        return None
    raw = data[offset] | (data[offset + 1] << 8)
    mantissa = raw & 0x0FFF
    if mantissa >= 0x0800:
        mantissa -= 0x1000
    exponent = (raw >> 12) & 0x0F
    if exponent >= 0x08:
        exponent -= 0x10
    return mantissa * (10.0 ** exponent)


def encode_result(result_data):
    """0x2A35：Flags 0x04 + SBP + DBP + MAP + HR，共 9 字节。

    仅接受 SBP/DBP/MAP/HR 全部有效的 ``dict``；否则返回 ``None``。
    不发 0、不发空结果、不发 HR 缺失的结果。
    """
    if not isinstance(result_data, dict):
        return None

    sbp = _to_float(result_data.get("sbp"))
    dbp = _to_float(result_data.get("dbp"))
    map_value = _to_float(result_data.get("map"))
    hr = _to_float(result_data.get("hr"))

    if sbp is None or dbp is None or map_value is None or hr is None:
        return None
    for value in (sbp, dbp, map_value):
        if _is_nan(value) or value <= 0.0 or value > MAX_PRESSURE_MMHG:
            return None
    if _is_nan(hr) or hr < MIN_HR_BPM or hr > MAX_HR_BPM:
        return None

    sbp_bytes = encode_measurement_sfloat(sbp)
    dbp_bytes = encode_measurement_sfloat(dbp)
    map_bytes = encode_measurement_sfloat(map_value)
    hr_bytes = encode_sfloat(int(round(hr)), 0)
    if sbp_bytes is None or dbp_bytes is None or map_bytes is None or hr_bytes is None:
        return None

    return bytes((0x04,)) + sbp_bytes + dbp_bytes + map_bytes + hr_bytes


def state_code(state, result=None):
    """把 FSM 状态（+DONE 结果）映射到 GATT 状态码 0–8。"""
    if state == config.STATE_DONE:
        if result == config.RESULT_OK:
            return 4
        if result in (config.RESULT_REJECT, config.RESULT_FAULT):
            return 5
        if result == config.RESULT_CANCEL:
            return 0
        return None
    return _STATE_TO_CODE.get(state)


def encode_status(state, result=None):
    code = state_code(state, result)
    if code is None:
        return None
    return bytes((code,))


def result_error_code(result, reason=None):
    """把 DONE 结果映射到 5 个错误码；OK/CANCEL 返回 ``None``。

    REJECT 统一 0x03（测量干扰/判废），不按 QC 文本细分；
    FAULT 按用户可行动的 reason 分类，未知归 0x02。
    """
    if result == config.RESULT_REJECT:
        return ERROR_QUALITY
    if result == config.RESULT_FAULT:
        return _FAULT_REASON_TO_ERROR.get(reason, ERROR_DEVICE)
    return None


def encode_error(result, reason=None):
    code = result_error_code(result, reason)
    if code is None:
        return None
    return bytes((code,))


def decode_control(data):
    """控制特征：仅接受 1 字节 0x01/0x02；其他返回 ``None``。"""
    if data is None:
        return None
    try:
        if len(data) != 1:
            return None
        value = data[0]
    except (TypeError, IndexError):
        return None
    if value == CONTROL_START:
        return EVENT_START
    if value == CONTROL_STOP:
        return EVENT_STOP
    return None
