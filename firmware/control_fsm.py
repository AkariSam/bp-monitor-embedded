"""测量控制状态机第一阶段：8 状态、全局安全出口、可注入分析器。

驱动只负责压力采样、泵阀动作与显示/通信原语；控制层负责全部状态迁移、
目标压、超时、故障判定和结果承载。所有时间比较使用 driver.now_ms()，
不使用 sleep()，因此可在 MicroPython 主循环中逐步调用 ``step()``。

全局安全出口（STOP / 严重故障）：
    safe_state() -> 压力未知/过期或 >=15 mmHg 则 DUMP，否则 DONE(CANCEL/FAULT)。
    safe_state() 失败时停留在 DUMP 持续重试，成功前不进入 DONE。
STOP/FAULT 路径不进入 ANALYZE，不分析已有 DEFLATE 缓冲。
"""

import config
from buffer import DeflateBuffer


def default_analyze_deflate(buffer):
    """算法移植前的占位分析器：统一判废，避免静默出数。"""
    return (config.RESULT_REJECT, config.REASON_ANALYZER_NOT_IMPLEMENTED, None)


class ControlFSM(object):
    """驱动无关的测量状态机。"""

    def __init__(self, driver, analyzer=None, analyze_deflate=None, max_deflate_samples=None):
        if analyzer is not None and analyze_deflate is not None:
            raise ValueError("analyzer 与 analyze_deflate 只能提供一个")
        if analyze_deflate is not None:
            analyzer = analyze_deflate
        if analyzer is None:
            analyzer = default_analyze_deflate

        self.driver = driver
        self.analyzer = analyzer

        self.state = config.STATE_IDLE
        self.state_entered_ms = driver.now_ms()
        self.measurement_start_ms = None

        self.result = None
        self.reason = None
        self.result_data = None
        self.last_pressure = None
        self.last_status = config.STATUS_OK
        self.last_valid_pressure_ms = None
        if max_deflate_samples is None:
            max_deflate_samples = config.MAX_DEFLATE_SAMPLES
        self.buffer = DeflateBuffer(max_deflate_samples)
        self.log = []

        self.pump_on = False
        self.valve_energized = False
        self.display_errors = 0
        self.ble_errors = 0
        self.event_poll_errors = 0
        self.safe_state_errors = 0
        self.actuator_errors = 0

        self._pending_start = False
        self._pending_stop = False
        self._pending_confirm = False
        self._analysis_required = False
        self._abort_result = None
        self._abort_reason = None
        self._total_timeout_fired = False
        self._overpressure_260 = False
        self._overpressure_280 = False
        self._deflate_window_ms = None
        self._deflate_window_p = None
        self._not_ready_since_ms = None
        self._not_ready_faulted = False
        self._last_read_was_not_ready = False
        self._safe_state_pending = False
        self._safe_state_error_active = False
        self._dump_timeout_pending_logged = False
        self._pump_error_active = False
        self._valve_error_active = False
        self._analyzer_started = False
        self._analyzer_is_session = False
        self._last_appended_seq = None

    # ------------------------------------------------------------------
    # 公共事件入口（非阻塞；事件在下次 step() 处理）
    # ------------------------------------------------------------------
    def start(self):
        self._pending_start = True

    def stop(self):
        self._pending_stop = True

    def confirm(self):
        self._pending_confirm = True

    def is_healthy(self):
        """安全动作已完成且无待重试项时为主循环健康状态。"""
        return not self._safe_state_pending

    def step(self):
        """推进一个控制步；每次调用最多完成一次事件处理或一次状态动作。"""
        self._retry_safe_state()
        self._poll_driver_events()
        if self._pending_stop:
            self._pending_stop = False
            self._pending_start = False
            self._pending_confirm = False
            self._handle_stop()
            return
        if self._pending_confirm:
            self._pending_confirm = False
            self._pending_start = False
            self._handle_confirm()
            return
        if self._pending_start:
            self._pending_start = False
            self._handle_start()
            return
        self._run_state()

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _poll_driver_events(self):
        if hasattr(self.driver, "poll_events"):
            try:
                events = self.driver.poll_events()
                self._consume_events(events)
            except Exception:
                self.event_poll_errors += 1

        if hasattr(self.driver, "ble_poll"):
            try:
                events = self.driver.ble_poll()
                self._consume_events(events)
            except Exception:
                self.ble_errors += 1

    def _consume_events(self, events):
        if events is None:
            return
        if isinstance(events, (str, bytes, bytearray)):
            events = (events,)
        elif isinstance(events, dict):
            # dict 迭代会得到键，容易把配置键误当事件；显式忽略。
            return
        else:
            try:
                events = iter(events)
            except TypeError:
                return
        for event in events:
            if isinstance(event, (bytes, bytearray)):
                try:
                    event = event.decode()
                except Exception:
                    continue
            if not isinstance(event, str):
                continue
            name = event.strip().upper()
            if name == "START":
                self.start()
            elif name == "STOP":
                self.stop()
            elif name == "CONFIRM":
                self.confirm()

    def _handle_start(self):
        if self.state != config.STATE_IDLE and self.state != config.STATE_DONE:
            self._log("START_IGNORED")
            return
        if not self._safe_state():
            self._log("START_SAFE_STATE_FAILED")
            return
        self._reset_cycle()
        self._enter(config.STATE_SELF_CHECK, "START")

    def _handle_stop(self):
        if self.state in config.MEASUREMENT_STATES:
            self._log("STOP")
            # STOP 先做安全动作，再读压力决定 DUMP/DONE；同 tick 超压故障仍优先。
            self._safe_state()
            self._read_and_guard()
            if self._abort_result == config.RESULT_FAULT:
                return
            self._abort(config.RESULT_CANCEL, config.REASON_USER_CANCEL)
        elif self.state == config.STATE_DONE:
            self._to_idle("STOP_IN_DONE")
        else:
            self._safe_state()
            self._log("STOP_IN_IDLE")

    def _handle_confirm(self):
        if self.state == config.STATE_DONE:
            self._to_idle("CONFIRM")
        else:
            self._log("CONFIRM_IGNORED")

    def _reset_cycle(self):
        self.buffer.clear()
        self.result = None
        self.reason = None
        self.result_data = None
        self.last_pressure = None
        self.last_valid_pressure_ms = None
        self.last_status = config.STATUS_OK
        self.measurement_start_ms = self._now()
        self._analysis_required = True
        self._abort_result = None
        self._abort_reason = None
        self._total_timeout_fired = False
        self._overpressure_260 = False
        self._overpressure_280 = False
        self._deflate_window_ms = None
        self._deflate_window_p = None
        self._not_ready_since_ms = None
        self._not_ready_faulted = False
        self._last_read_was_not_ready = False
        self._safe_state_pending = False
        self._safe_state_error_active = False
        self._dump_timeout_pending_logged = False
        self._pump_error_active = False
        self._valve_error_active = False
        self._analyzer_started = False
        self._analyzer_is_session = False
        self._last_appended_seq = None

    def _to_idle(self, event):
        if not self._safe_state():
            self._log("CONFIRM_SAFE_STATE_FAILED")
            return
        self._analysis_required = False
        self._abort_result = None
        self._abort_reason = None
        self.result = None
        self.reason = None
        self.result_data = None
        self.buffer.clear()
        self.measurement_start_ms = None
        self._not_ready_since_ms = None
        self._not_ready_faulted = False
        self._last_read_was_not_ready = False
        self._safe_state_error_active = False
        self._dump_timeout_pending_logged = False
        self._pump_error_active = False
        self._valve_error_active = False
        self._analyzer_started = False
        self._analyzer_is_session = False
        self._last_appended_seq = None
        self._enter(config.STATE_IDLE, event)

    # ------------------------------------------------------------------
    # 状态分发
    # ------------------------------------------------------------------
    def _run_state(self):
        if self.state == config.STATE_IDLE or self.state == config.STATE_DONE:
            return
        if not self._total_timeout_fired and self.measurement_start_ms is not None:
            elapsed = self._now() - self.measurement_start_ms
            if elapsed > config.TOTAL_MEASUREMENT_TIMEOUT_MS:
                self._total_timeout_fired = True
                self._fault(config.REASON_TOTAL_TIMEOUT)
                return

        if self.state == config.STATE_SELF_CHECK:
            self._step_self_check()
        elif self.state == config.STATE_INFLATE:
            self._step_inflate()
        elif self.state == config.STATE_SETTLE:
            self._step_settle()
        elif self.state == config.STATE_DEFLATE:
            self._step_deflate()
        elif self.state == config.STATE_DUMP:
            self._step_dump()
        elif self.state == config.STATE_ANALYZE:
            self._step_analyze()

    # ------------------------------------------------------------------
    # 各状态动作
    # ------------------------------------------------------------------
    def _step_self_check(self):
        sample = self._read_and_guard()
        if self.state != config.STATE_SELF_CHECK:
            return
        # 自检总预算固定为 SELF_CHECK_TIMEOUT_MS；即使最后时刻恢复有效也超时。
        if self._now() - self.state_entered_ms > config.SELF_CHECK_TIMEOUT_MS:
            self._fault(config.REASON_SELF_CHECK_TIMEOUT)
            return
        if sample is None:
            return
        p = sample[2]
        if p > config.SELF_CHECK_MAX_PRESSURE_MMHG:
            self._log("SELF_CHECK_PRESSURE_HIGH")
            self._fault(config.REASON_SELF_CHECK_PRESSURE_HIGH)
            return
        if not self._valve_set(True):
            self._actuator_fault()
            return
        if not self._pump_set(True):
            self._actuator_fault()
            return
        self._enter(config.STATE_INFLATE, "SELF_CHECK_OK")

    def _step_inflate(self):
        sample = self._read_and_guard()
        if self.state != config.STATE_INFLATE:
            return
        if sample is None:
            if self._last_read_was_not_ready:
                if self.pump_on and not self._pump_set(False):
                    self._actuator_fault()
                    return
                if self._track_not_ready():
                    return
            return
        self._clear_not_ready()
        if sample[2] >= config.TARGET_PRESSURE_MMHG:
            if self.pump_on and not self._pump_set(False):
                self._actuator_fault()
                return
            self._log("TARGET_REACHED")
            self._enter(config.STATE_SETTLE, "INFLATE_TARGET")
            return
        if not self.pump_on and not self._pump_set(True):
            self._actuator_fault()
            return
        if self._now() - self.state_entered_ms > config.INFLATE_TIMEOUT_MS:
            self._fault(config.REASON_INFLATE_TIMEOUT)

    def _step_settle(self):
        sample = self._read_and_guard()
        if self.state != config.STATE_SETTLE:
            return
        if sample is None:
            if self._track_not_ready():
                return
        else:
            self._clear_not_ready()
        if self._now() - self.state_entered_ms >= config.SETTLE_MS:
            self.buffer.clear()
            self._last_appended_seq = None
            self._deflate_window_ms = self._now()
            self._deflate_window_p = None
            self._enter(config.STATE_DEFLATE, "SETTLE_DONE")

    def _step_deflate(self):
        sample = self._read_and_guard()
        if self.state != config.STATE_DEFLATE:
            return
        if sample is None:
            if self._track_not_ready():
                return
            return

        self._clear_not_ready()
        seq = sample[0]
        t_ms = sample[1]
        p = sample[2]

        append_sample = True
        if self._last_appended_seq is not None:
            if seq == self._last_appended_seq:
                append_sample = False
            elif seq < self._last_appended_seq:
                self._log("SEQ_REGRESSION")
                self._fault(config.REASON_SENSOR_SEQ_REGRESSION)
                return
        if append_sample:
            try:
                appended = self.buffer.append(seq, t_ms, p)
            except Exception:
                self._fault(config.REASON_BUFFER_OVERFLOW)
                return
            if not appended:
                self._fault(config.REASON_BUFFER_OVERFLOW)
                return
            self._last_appended_seq = seq

        # 先判测量段结束，再做卡死窗口，避免 p 刚跨过 40 时被误判。
        if p < config.DEFLATE_END_PRESSURE_MMHG:
            if not self._valve_set(False):
                self._actuator_fault()
                return
            self._enter(config.STATE_DUMP, "DEFLATE_END")
            return

        now = self._now()
        if self._deflate_window_p is None:
            self._deflate_window_p = p
            self._deflate_window_ms = now
        elif now - self._deflate_window_ms >= config.DEFLATE_STALL_WINDOW_MS:
            drop = self._deflate_window_p - p
            if drop < config.DEFLATE_STALL_MIN_DROP_MMHG:
                self._fault(config.REASON_DEFLATE_STUCK)
                return
            self._deflate_window_ms = now
            self._deflate_window_p = p

    def _step_dump(self):
        # step() 开头已统一重试 safe_state；即使 pending 未清，也继续读压力监督。
        sample = self._read_and_guard()
        if self.state != config.STATE_DUMP:
            return

        timed_out = (
            self._now() - self.state_entered_ms >= config.DUMP_TIMEOUT_MS
        )
        if sample is None:
            if self._last_read_was_not_ready:
                self._track_not_ready()
                if self.state != config.STATE_DUMP:
                    return
            if timed_out:
                # 无法确认压力已降到安全线以下；safe_state 成功后才 DONE。
                self._dump_timeout()
            return

        self._clear_not_ready()
        p = sample[2]
        if timed_out and (
            p >= config.ANALYZE_PRESSURE_MMHG or self._safe_state_pending
        ):
            self._dump_timeout()
            return
        if p >= config.ANALYZE_PRESSURE_MMHG:
            return

        if self._safe_state_pending:
            return

        if self._abort_result == config.RESULT_FAULT:
            self._enter_done(config.RESULT_FAULT, self._abort_reason)
        elif self._abort_result == config.RESULT_CANCEL:
            self._enter_done(config.RESULT_CANCEL, self._abort_reason)
        elif self._analysis_required:
            self._enter(config.STATE_ANALYZE, "DUMP_DONE")
        else:
            self._enter_done(config.RESULT_REJECT, config.REASON_NO_ANALYSIS)

    def _dump_timeout(self):
        """DUMP 兜底：safe_state 成功后才 DONE；保留已有故障根因。"""
        if not self._safe_state():
            self._analysis_required = False
            if self._abort_result != config.RESULT_FAULT:
                self._abort_result = config.RESULT_FAULT
                self._abort_reason = config.REASON_SAFE_STATE_FAILED
            if not self._dump_timeout_pending_logged:
                self._log("DUMP_TIMEOUT_SAFE_STATE_PENDING")
                self._dump_timeout_pending_logged = True
            return
        self._analysis_required = False
        self._dump_timeout_pending_logged = False
        report_reason = self._abort_reason
        if self._abort_result != config.RESULT_FAULT or report_reason is None:
            report_reason = config.REASON_DUMP_TIMEOUT
        self._abort_result = config.RESULT_FAULT
        self._abort_reason = report_reason
        self._log("DUMP_TIMEOUT")
        self._enter_done(config.RESULT_FAULT, report_reason)

    def _step_analyze(self):
        """函数型 analyzer 一步完成；带 start(buffer)/step() 的对象按阶段执行。

        step() 返回 None 表示尚未完成，返回分析结果则进入 DONE；异常统一 REJECT。
        """
        if not self._analysis_required:
            self._enter_done(config.RESULT_REJECT, config.REASON_NO_ANALYSIS)
            return
        if not self._analyzer_started:
            self._analyzer_started = True
            self._log("ANALYZE_START")
            self._analyzer_is_session = (
                hasattr(self.analyzer, "start") and hasattr(self.analyzer, "step")
            )
            if self._analyzer_is_session:
                try:
                    self.analyzer.start(self.buffer)
                except Exception:
                    self._log("ANALYZE_EXCEPTION")
                    self._enter_done(config.RESULT_REJECT, config.REASON_ANALYZER_ERROR)
                return
            try:
                raw = self.analyzer(self.buffer)
            except Exception:
                self._log("ANALYZE_EXCEPTION")
                self._enter_done(config.RESULT_REJECT, config.REASON_ANALYZER_ERROR)
                return
            result, reason, data = self._normalize_analyzer_result(raw)
            self._enter_done(result, reason, data)
            return

        # 分阶段会话：每个控制步只执行一个有界阶段，STOP 可在阶段间生效。
        try:
            raw = self.analyzer.step()
        except Exception:
            self._log("ANALYZE_EXCEPTION")
            self._enter_done(config.RESULT_REJECT, config.REASON_ANALYZER_ERROR)
            return
        if raw is None:
            return
        result, reason, data = self._normalize_analyzer_result(raw)
        self._enter_done(result, reason, data)

    # ------------------------------------------------------------------
    # 采样、安全监督
    # ------------------------------------------------------------------
    def _read_and_guard(self):
        """读取压力并执行状态位/超压检查。

        返回 ``(seq, t_ms, p, status)`` 仅当样本可用于控制；NOT_READY 或
        严重故障返回 None。严重故障已在本函数内触发安全出口。
        """
        self._last_read_was_not_ready = False
        try:
            sample = self.driver.pressure_read()
            seq = sample[0]
            t_ms = sample[1]
            p = float(sample[2])
            status = int(sample[3])
        except Exception:
            self._log("PRESSURE_READ_EXCEPTION")
            self._fail_sensor(config.REASON_SENSOR_COMM_FAIL)
            return None

        self.last_status = status

        if status & config.STATUS_SAFETY_FAULTS:
            self._log("SENSOR_FAULT")
            self._fail_sensor(self._status_reason(status))
            return None
        if status & ~config.STATUS_KNOWN:
            self._log("SENSOR_UNKNOWN_STATUS")
            self._fail_sensor(config.REASON_SENSOR_OUT_OF_RANGE)
            return None
        if status & config.STATUS_NOT_READY:
            self._last_read_was_not_ready = True
            return None
        if p != p:  # NaN
            self._log("PRESSURE_NAN")
            self._fail_sensor(config.REASON_SENSOR_OUT_OF_RANGE)
            return None
        if p < config.MIN_VALID_PRESSURE_MMHG:
            self._log("PRESSURE_BELOW_RANGE")
            self._fail_sensor(config.REASON_SENSOR_OUT_OF_RANGE)
            return None

        self.last_pressure = p
        self.last_valid_pressure_ms = self._now()
        if self._check_overpressure(p):
            return None
        return (seq, t_ms, p, status)

    def _status_reason(self, status):
        if status & config.STATUS_COMM_FAIL:
            return config.REASON_SENSOR_COMM_FAIL
        if status & config.STATUS_STALE:
            return config.REASON_SENSOR_STALE
        if status & config.STATUS_SATURATED:
            return config.REASON_SENSOR_SATURATED
        return config.REASON_SENSOR_OUT_OF_RANGE

    def _check_overpressure(self, p):
        if p >= config.OVERPRESSURE_LATCH_MMHG:
            self._overpressure_260 = True
            if not self._overpressure_280:
                self._overpressure_280 = True
                self._log("OVERPRESSURE_280")
                self._fault(config.REASON_OVERPRESSURE_LATCHED_280)
                return True
            return False
        if p >= config.OVERPRESSURE_FAULT_MMHG:
            if not self._overpressure_260:
                self._overpressure_260 = True
                self._log("OVERPRESSURE_260")
                self._fault(config.REASON_OVERPRESSURE_260)
                return True
        return False

    def _fault(self, reason):
        self._log("FAULT_" + reason)
        self._abort(config.RESULT_FAULT, reason)

    def _abort(self, result, reason):
        """全局安全出口：先 safe_state，再按压力有效性决定 DUMP 或 DONE。"""
        safe_ok = self._safe_state()
        self._analysis_required = False

        if result == config.RESULT_FAULT:
            if self._abort_result != config.RESULT_FAULT:
                self._abort_result = config.RESULT_FAULT
                self._abort_reason = reason
            elif reason == config.REASON_OVERPRESSURE_LATCHED_280:
                self._abort_reason = reason
            elif self._abort_reason is None:
                self._abort_reason = reason
        elif self._abort_result != config.RESULT_FAULT:
            if safe_ok:
                if self._abort_result is None:
                    self._abort_result = config.RESULT_CANCEL
                    self._abort_reason = reason
            else:
                self._abort_result = config.RESULT_FAULT
                self._abort_reason = config.REASON_SAFE_STATE_FAILED

        self._log("ABORT_" + result)

        if not safe_ok:
            if self.state == config.STATE_DUMP:
                return
            self._enter(config.STATE_DUMP, "ABORT_DUMP")
            return

        if (
            self._pressure_known_recent()
            and self.last_pressure < config.ANALYZE_PRESSURE_MMHG
        ):
            self._enter_done(self._abort_result, self._abort_reason)
        elif self.state == config.STATE_DUMP:
            # 已在快排段，等压力降到安全线以下再结束。
            return
        else:
            self._enter(config.STATE_DUMP, "ABORT_DUMP")

    def _fail_sensor(self, reason):
        """传感器严重故障；DUMP 中继续重试安全动作，不直接 DONE。"""
        if self.state == config.STATE_DUMP:
            self._log("SENSOR_FAULT_IN_DUMP")
            self._safe_state()
            self._analysis_required = False
            if self._abort_result != config.RESULT_FAULT:
                self._abort_result = config.RESULT_FAULT
                self._abort_reason = reason
            elif self._abort_reason is None:
                self._abort_reason = reason
        else:
            self._fault(reason)

    def _pressure_known_recent(self):
        if self.last_pressure is None or self.last_valid_pressure_ms is None:
            return False
        age = self._now() - self.last_valid_pressure_ms
        return age <= config.SENSOR_NOT_READY_TIMEOUT_MS

    def _track_not_ready(self):
        """测量期 NOT_READY 连续超时判定；SELF_CHECK 不调用本函数。"""
        if self._not_ready_faulted:
            return False
        if not self._last_read_was_not_ready:
            return False
        now = self._now()
        if self._not_ready_since_ms is None:
            self._not_ready_since_ms = now
        if now - self._not_ready_since_ms > config.SENSOR_NOT_READY_TIMEOUT_MS:
            self._not_ready_faulted = True
            self._not_ready_since_ms = None
            self._fault(config.REASON_SENSOR_NOT_READY_TIMEOUT)
            return True
        return False

    def _clear_not_ready(self):
        self._not_ready_since_ms = None

    def _retry_safe_state(self):
        if not self._safe_state_pending:
            return True
        if self._safe_state():
            self._log("SAFE_STATE_RECOVERED")
            return True
        return False

    def _actuator_fault(self):
        self._fault(config.REASON_ACTUATOR_FAILED)

    # ------------------------------------------------------------------
    # 状态迁移、结果与辅助
    # ------------------------------------------------------------------
    def _enter(self, state, event):
        self.state = state
        self.state_entered_ms = self._now()
        self._log(event)
        self._publish()

    def _enter_done(self, result, reason, data=None):
        self.result = result
        self.reason = reason
        self.result_data = data
        self._analysis_required = False
        self._enter(config.STATE_DONE, "DONE_" + result)

    def _normalize_analyzer_result(self, raw):
        verdict = None
        reason = None
        data = None

        if isinstance(raw, str):
            verdict = raw
        elif isinstance(raw, (tuple, list)):
            if len(raw) >= 1:
                verdict = raw[0]
            if len(raw) >= 2:
                reason = raw[1]
            if len(raw) >= 3:
                data = raw[2]
        elif isinstance(raw, dict):
            verdict = raw.get("result")
            reason = raw.get("reason")
            data = raw.get("data")

        if verdict == config.RESULT_OK:
            if reason is None:
                reason = config.REASON_NONE
            return (config.RESULT_OK, reason, data)
        if verdict == config.RESULT_REJECT:
            if reason is None:
                reason = config.REASON_ANALYZER_REJECT
            return (config.RESULT_REJECT, reason, data)
        if raw is None:
            return (config.RESULT_REJECT, config.REASON_ANALYZER_NO_RESULT, data)
        return (config.RESULT_REJECT, config.REASON_ANALYZER_BAD_RESULT, data)

    def _safe_state(self):
        try:
            self.driver.safe_state()
        except Exception:
            self.safe_state_errors += 1
            if not self._safe_state_error_active:
                self._log("SAFE_STATE_ERROR")
                self._safe_state_error_active = True
            # 已关闭的通道不依赖 setter：setter 失败不应把安全动作判为未完成。
            pump_ok = (not self.pump_on) or self._pump_set(False)
            valve_ok = (not self.valve_energized) or self._valve_set(False)
            if pump_ok and valve_ok:
                self._safe_state_pending = False
                self._safe_state_error_active = False
                self._log("SAFE_STATE_FALLBACK_OK")
                return True
            self._safe_state_pending = True
            return False
        self._safe_state_error_active = False
        self.pump_on = False
        self.valve_energized = False
        self._safe_state_pending = False
        return True

    def _pump_set(self, on):
        try:
            self.driver.pump_set(on)
        except Exception:
            self.actuator_errors += 1
            if not self._pump_error_active:
                self._log("PUMP_SET_ERROR")
                self._pump_error_active = True
            return False
        self._pump_error_active = False
        self.pump_on = bool(on)
        return True

    def _valve_set(self, on):
        try:
            self.driver.valve_set_energized(on)
        except Exception:
            self.actuator_errors += 1
            if not self._valve_error_active:
                self._log("VALVE_SET_ERROR")
                self._valve_error_active = True
            return False
        self._valve_error_active = False
        self.valve_energized = bool(on)
        return True

    def _publish(self):
        frame = {
            "type": "STATE",
            "t_ms": self._now(),
            "state": self.state,
            "result": self.result,
            "reason": self.reason,
            "p_mmHg": self.last_pressure,
            "pump": self.pump_on,
            "valve": self.valve_energized,
            "data": (
                self.result_data
                if self.state == config.STATE_DONE
                and self.result == config.RESULT_OK
                else None
            ),
        }
        try:
            self.driver.display_render(frame)
        except Exception:
            self.display_errors += 1
        try:
            self.driver.ble_send(frame)
        except Exception:
            self.ble_errors += 1

    def _log(self, event):
        if len(self.log) >= config.MAX_LOG_ENTRIES:
            self.log.pop(0)
        self.log.append(
            {
                "t_ms": self._now(),
                "p_mmHg": self.last_pressure,
                "state": self.state,
                "pump": self.pump_on,
                "valve": self.valve_energized,
                "event": event,
            }
        )

    def _now(self):
        return self.driver.now_ms()
