"""假驱动与假时钟：在 PC 上验证控制层，不依赖硬件。

压力模型：
- 泵开且阀通电：按 pump_rate 升压；
- 泵停且阀通电：按 leak_rate 自然慢降（模拟气路自然漏气）；
- 阀失电：按 dump_rate 快速泄压（泵开时先抵消泵速，但安全路径总是先停泵）。

所有状态变化只由 FakeClock.advance(ms) 推进，测试可完全确定地复现时序。
"""

import config


class FakeClock(object):
    """可手动推进的单调时钟（毫秒）。"""

    def __init__(self, start_ms=0):
        self._t_ms = int(start_ms)
        self._listeners = []

    def now_ms(self):
        return self._t_ms

    def advance(self, ms):
        ms = int(ms)
        if ms < 0:
            raise ValueError("FakeClock.advance 不接受负值")
        self._t_ms += ms
        for listener in self._listeners:
            listener(self._t_ms)

    def add_listener(self, listener):
        self._listeners.append(listener)


class FakeDriver(object):
    """最小驱动接口的假实现，记录泵/阀/安全动作供断言。"""

    def __init__(
        self,
        clock=None,
        pressure_mmHg=0.0,
        pump_rate_mmhg_s=30.0,
        leak_rate_mmhg_s=3.0,
        dump_rate_mmhg_s=80.0,
        sensor_status=config.STATUS_OK,
        reads_per_sample=1,
    ):
        if clock is None:
            clock = FakeClock()
        self.clock = clock
        self.pressure_mmHg = float(pressure_mmHg)
        self.pump_rate_mmhg_s = float(pump_rate_mmhg_s)
        self.leak_rate_mmhg_s = float(leak_rate_mmhg_s)
        self.dump_rate_mmhg_s = float(dump_rate_mmhg_s)
        self.sensor_status = int(sensor_status)
        self.reads_per_sample = max(1, int(reads_per_sample))

        self.pump_on = False
        self.valve_energized = False
        self.safe_state_calls = 0
        self.pump_set_calls = 0
        self.valve_set_calls = 0
        self.pressure_read_calls = 0
        self.operation_log = []
        self.pump_history = []
        self.valve_history = []
        self.safe_state_history = []

        self.display_views = []
        self.ble_frames = []
        self.fail_display = False
        self.fail_ble = False
        self.fail_safe_state = False
        self.fail_pump = False
        self.fail_valve = False
        self.raise_on_pressure_read = False

        self._seq = 0
        self._read_count = 0
        self._cached_sample = None
        self._events = []
        self._ble_events = []
        self._last_update_ms = self.clock.now_ms()
        self.clock.add_listener(self._on_clock_advance)

    # ---- 时钟与压力模型 ----
    def now_ms(self):
        return self.clock.now_ms()

    def advance(self, ms):
        self.clock.advance(ms)

    def _on_clock_advance(self, t_ms):
        dt = (t_ms - self._last_update_ms) / 1000.0
        self._last_update_ms = t_ms
        if dt <= 0.0:
            return
        if self.pump_on:
            if self.valve_energized:
                delta = self.pump_rate_mmhg_s * dt
            else:
                delta = (self.pump_rate_mmhg_s - self.dump_rate_mmhg_s) * dt
        elif self.valve_energized:
            delta = -self.leak_rate_mmhg_s * dt
        else:
            delta = -self.dump_rate_mmhg_s * dt
        self.pressure_mmHg += delta
        if self.pressure_mmHg < 0.0:
            self.pressure_mmHg = 0.0

    def set_pressure(self, p_mmHg):
        self.pressure_mmHg = float(p_mmHg)
        self._last_update_ms = self.clock.now_ms()
        self._cached_sample = None

    def set_pump_rate(self, rate_mmhg_s):
        self.pump_rate_mmhg_s = float(rate_mmhg_s)

    def set_leak_rate(self, rate_mmhg_s):
        self.leak_rate_mmhg_s = float(rate_mmhg_s)

    def set_dump_rate(self, rate_mmhg_s):
        self.dump_rate_mmhg_s = float(rate_mmhg_s)

    def set_sensor_status(self, status):
        self.sensor_status = int(status)
        self._cached_sample = None

    def set_reads_per_sample(self, reads):
        self.reads_per_sample = max(1, int(reads))
        self._cached_sample = None

    def reset_seq(self, value=0):
        """测试用：强制 seq 复位/倒退，下一次读取生成新样本。"""
        self._seq = int(value)
        self._cached_sample = None

    # ---- 最小驱动接口 ----
    def pressure_read(self):
        self.operation_log.append("pressure_read")
        if self.raise_on_pressure_read:
            raise RuntimeError("pressure sensor offline")
        self.pressure_read_calls += 1
        self._read_count += 1
        if self._cached_sample is None or self._read_count % self.reads_per_sample == 0:
            seq = self._seq
            self._seq += 1
            self._cached_sample = (
                seq,
                self.clock.now_ms(),
                round(self.pressure_mmHg, 2),
                self.sensor_status,
            )
        return self._cached_sample

    def pump_set(self, on):
        on = bool(on)
        self.operation_log.append("pump_set")
        self.pump_set_calls += 1
        self.pump_history.append((self.clock.now_ms(), on))
        if self.fail_pump:
            raise RuntimeError("pump driver failed")
        self.pump_on = on

    def valve_set_energized(self, on):
        on = bool(on)
        self.operation_log.append("valve_set")
        self.valve_set_calls += 1
        self.valve_history.append((self.clock.now_ms(), on))
        if self.fail_valve:
            raise RuntimeError("valve driver failed")
        self.valve_energized = on

    def safe_state(self):
        self.operation_log.append("safe_state")
        self.safe_state_calls += 1
        self.safe_state_history.append(
            (self.clock.now_ms(), self.pump_on, self.valve_energized)
        )
        if self.fail_safe_state:
            raise RuntimeError("safe_state failed")
        self.pump_on = False
        self.valve_energized = False

    def display_render(self, view):
        if self.fail_display:
            raise RuntimeError("display offline")
        self.display_views.append(view)

    def ble_send(self, frame):
        if self.fail_ble:
            raise RuntimeError("ble offline")
        self.ble_frames.append(frame)

    def ble_poll(self):
        if self.fail_ble:
            raise RuntimeError("ble offline")
        events = self._ble_events
        self._ble_events = []
        return events

    # ---- 可选：输入事件队列（真实驱动当前不要求实现） ----
    def queue_event(self, event):
        self._events.append(event)

    def queue_stop(self):
        self.queue_event("STOP")

    def poll_events(self):
        events = self._events
        self._events = []
        return events

    def queue_ble_event(self, event):
        if isinstance(event, (list, tuple)):
            self._ble_events.extend(event)
        else:
            self._ble_events.append(event)

    def queue_ble_stop(self):
        self.queue_ble_event("STOP")

    # ---- 脚本化场景开关 ----
    def configure_normal(self):
        self.pump_rate_mmhg_s = 30.0
        self.leak_rate_mmhg_s = 3.0
        self.dump_rate_mmhg_s = 80.0

    def configure_inflate_timeout(self):
        self.pump_rate_mmhg_s = 2.0

    def configure_deflate_stuck(self):
        self.leak_rate_mmhg_s = 0.0

    def configure_overpressure(self, p_mmHg=285.0):
        self.set_pressure(p_mmHg)

    def configure_sensor_fault(self, status=config.STATUS_COMM_FAIL):
        self.set_sensor_status(status)
