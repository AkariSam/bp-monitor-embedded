"""Phase 2A 合成测量序列（纯逻辑，不依赖 machine/aioble）。

产生的 frame 与 ``ControlFSM._publish`` 的字段保持一致，便于后续替换成真实
控制层。这里只负责状态、压力、结果和场景，不负责 BLE 发送。
"""

import config


SCENARIO_OK = "OK"
SCENARIO_REJECT = "REJECT"
SCENARIO_FAULT = "FAULT"
SCENARIOS = (SCENARIO_OK, SCENARIO_REJECT, SCENARIO_FAULT)

SELF_CHECK_MS = 1000
INFLATE_MS = 5000
SETTLE_MS = 1000
DEFLATE_MS = 5000
ANALYZE_MS = 1000
DUMP_MS = 2000

OK_DATA = {"sbp": 120.0, "dbp": 80.0, "map": 93.0, "hr": 72}


class SyntheticBench(object):
    """确定性的合成血压测量源。

    每次 START 按 OK → REJECT → FAULT 循环；STOP 走 DUMP → IDLE。
    """

    def __init__(self):
        self.t_ms = 0
        self.state = config.STATE_IDLE
        self.result = None
        self.reason = None
        self.result_data = None
        self.scenario = None
        self.scenario_index = 0
        self.phase_ms = 0
        self._pending_frame = None

    # ---- 控制 ----

    def handle_event(self, event):
        if event == "START":
            return self.start()
        if event == "STOP":
            return self.stop()
        return False

    def start(self):
        if self.state not in (config.STATE_IDLE, config.STATE_DONE):
            return False
        self.scenario = SCENARIOS[self.scenario_index % len(SCENARIOS)]
        self.scenario_index += 1
        self.result = None
        self.reason = None
        self.result_data = None
        self.phase_ms = 0
        self._enter(config.STATE_SELF_CHECK)
        return True

    def stop(self):
        if self.state in (config.STATE_IDLE, config.STATE_DONE, config.STATE_DUMP):
            return False
        self.result = None
        self.reason = None
        self.result_data = None
        self.phase_ms = 0
        self._enter(config.STATE_DUMP)
        return True

    # ---- 时间推进 ----

    def tick(self, dt_ms):
        if dt_ms <= 0:
            return
        self.t_ms += dt_ms
        self.phase_ms += dt_ms

        while True:
            duration = _state_duration(self.state)
            if duration is None or self.phase_ms < duration:
                break
            self.phase_ms -= duration
            self._advance()

    def _advance(self):
        if self.state == config.STATE_SELF_CHECK:
            self._enter(config.STATE_INFLATE)
        elif self.state == config.STATE_INFLATE:
            self._enter(config.STATE_SETTLE)
        elif self.state == config.STATE_SETTLE:
            self._enter(config.STATE_DEFLATE)
        elif self.state == config.STATE_DEFLATE:
            self._enter(config.STATE_ANALYZE)
        elif self.state == config.STATE_ANALYZE:
            self._finish()
        elif self.state == config.STATE_DUMP:
            self._enter(config.STATE_IDLE)

    def _enter(self, state):
        self.state = state
        self.phase_ms = 0
        self._pending_frame = self.current_frame()

    def _finish(self):
        self.state = config.STATE_DONE
        if self.scenario == SCENARIO_OK:
            self.result = config.RESULT_OK
            self.reason = None
            self.result_data = dict(OK_DATA)
        elif self.scenario == SCENARIO_REJECT:
            self.result = config.RESULT_REJECT
            self.reason = config.REASON_ANALYZER_REJECT
            self.result_data = None
        else:
            self.result = config.RESULT_FAULT
            self.reason = config.REASON_OVERPRESSURE_260
            self.result_data = None
        self.phase_ms = 0
        self._pending_frame = self.current_frame()

    # ---- 状态输出 ----

    def take_frame(self):
        frame = self._pending_frame
        self._pending_frame = None
        return frame

    @property
    def pressure_enabled(self):
        return self.state in (
            config.STATE_SELF_CHECK,
            config.STATE_INFLATE,
            config.STATE_SETTLE,
            config.STATE_DEFLATE,
            config.STATE_DUMP,
        )

    @property
    def pressure(self):
        if self.state == config.STATE_SELF_CHECK:
            return 0.0
        if self.state == config.STATE_INFLATE:
            return 180.0 * _progress(self.phase_ms, INFLATE_MS)
        if self.state == config.STATE_SETTLE:
            return 180.0
        if self.state == config.STATE_DEFLATE:
            return 180.0 - 140.0 * _progress(self.phase_ms, DEFLATE_MS)
        if self.state == config.STATE_DUMP:
            return 40.0 - 40.0 * _progress(self.phase_ms, DUMP_MS)
        return None

    def current_frame(self):
        is_done = self.state == config.STATE_DONE
        return {
            "type": "STATE",
            "t_ms": self.t_ms,
            "state": self.state,
            "result": self.result if is_done else None,
            "reason": self.reason if is_done else None,
            "p_mmHg": self.pressure,
            "pump": self.state == config.STATE_INFLATE,
            "valve": self.state in (
                config.STATE_SELF_CHECK,
                config.STATE_INFLATE,
                config.STATE_SETTLE,
                config.STATE_DEFLATE,
            ),
            "data": self.result_data if is_done and self.result == config.RESULT_OK else None,
        }


def _state_duration(state):
    if state == config.STATE_SELF_CHECK:
        return SELF_CHECK_MS
    if state == config.STATE_INFLATE:
        return INFLATE_MS
    if state == config.STATE_SETTLE:
        return SETTLE_MS
    if state == config.STATE_DEFLATE:
        return DEFLATE_MS
    if state == config.STATE_ANALYZE:
        return ANALYZE_MS
    if state == config.STATE_DUMP:
        return DUMP_MS
    return None


def _progress(elapsed_ms, duration_ms):
    value = float(elapsed_ms) / float(duration_ms)
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
