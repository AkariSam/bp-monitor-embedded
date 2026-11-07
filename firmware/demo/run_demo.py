"""PC 端控制层可核验演示。

直接驱动真实的 ``ControlFSM`` + ``FakeDriver``，逐步调用 ``fsm.step()``。
每个场景独立执行、独立检查并返回结构化结果；任一检查失败时场景为 FAIL，
``main()`` 返回非零退出码。

注意：基础 ``FakeDriver`` 没有脉搏波模型。场景 1 使用
``PulsatileFakeDriver``（FakeDriver 子类，仅向读数注入合成脉搏）驱动
真实的固件移植分析器；PC 参考管线对同一 ``DeflateBuffer`` 做 SBP/DBP 对拍。
其余场景继续使用未注入脉搏的 FakeDriver。
"""

import math
import sys
from pathlib import Path

FIRMWARE_DIR = Path(__file__).resolve().parents[1]
if str(FIRMWARE_DIR) not in sys.path:
    sys.path.insert(0, str(FIRMWARE_DIR))

ALGO_DIR = FIRMWARE_DIR.parent / "algo"
if str(ALGO_DIR) not in sys.path:
    sys.path.append(str(ALGO_DIR))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

import config
from analyzer import PipelineAnalyzer
from bp.csvio import Recording
from bp.pipeline import run as pc_pipeline_run
from control_fsm import ControlFSM
from drivers.fake_driver import FakeClock, FakeDriver

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "out"

STATE_COLORS = {
    config.STATE_IDLE: "#d9d9d9",
    config.STATE_SELF_CHECK: "#9ecae1",
    config.STATE_INFLATE: "#74c476",
    config.STATE_SETTLE: "#fee08b",
    config.STATE_DEFLATE: "#41ab5d",
    config.STATE_DUMP: "#fb6a4a",
    config.STATE_ANALYZE: "#bcbddc",
    config.STATE_DONE: "#bdbdbd",
}


class RecordingAnalyzer(object):
    """记录调用次数；场景 2–5 用它断言 ANALYZE 从未被调用。"""

    def __init__(self):
        self.calls = []
        self.last_result = None

    def __call__(self, buffer):
        self.calls.append(len(buffer))
        self.last_result = (
            config.RESULT_OK,
            config.REASON_NONE,
            {"demo": True, "n_samples": len(buffer)},
        )
        return self.last_result


class PulsatileFakeDriver(FakeDriver):
    """演示专用 FakeDriver 子类：仅向读数叠加合成脉搏。

    基础 FakeDriver 只有平滑 DC 压力模型。这里保留其泵阀/压力行为，只给
    pressure_read 的返回值加一个 1.2 Hz、振幅随袖带压变化的脉搏，使真实
    移植分析器可以在 demo 中运行；不用于精度评估。
    """

    def pressure_read(self):
        seq, t_ms, p_mmHg, status = FakeDriver.pressure_read(self)
        amp = 3.0 * math.exp(-((p_mmHg - 100.0) / 28.0) ** 2)
        pulse = amp * math.sin(2.0 * math.pi * 1.2 * (t_ms / 1000.0))
        return (seq, t_ms, p_mmHg + pulse, status)


def pc_reference_on_buffer(buffer):
    """用 PC 正本管线分析同一个 DeflateBuffer 的 DEFLATE 数据。"""
    n = len(buffer)
    t_ms = []
    p_raw = []
    for _seq, t, p in buffer:
        t_ms.append(float(t))
        p_raw.append(p)
    rec = Recording(
        t_ms=np.array(t_ms, dtype=float),
        p_raw=np.array(p_raw, dtype=float),
        phase=np.array(["DEFLATE"] * n),
    )
    return pc_pipeline_run(rec)


class ScenarioResult(object):
    def __init__(
        self,
        number,
        name,
        title,
        expected,
        actual,
        passed,
        failures,
        steps,
        elapsed_ms,
        plot_path,
        timeline,
        samples,
        injections,
    ):
        self.number = number
        self.name = name
        self.title = title
        self.expected = expected
        self.actual = actual
        self.passed = passed
        self.failures = failures
        self.steps = steps
        self.elapsed_ms = elapsed_ms
        self.plot_path = plot_path
        self.timeline = timeline
        self.samples = samples
        self.injections = injections

    def failure_text(self):
        if not self.failures:
            return "OK"
        return " | ".join(self.failures)


class ScenarioRun(object):
    """一个场景的 FakeClock/FakeDriver/ControlFSM 运行上下文。"""

    def __init__(
        self,
        number,
        name,
        title,
        analyzer,
        output_dir=None,
        make_plot=True,
        step_ms=12,
        driver_factory=None,
    ):
        self.number = number
        self.name = name
        self.title = title
        self.output_dir = (
            Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
        )
        self.make_plot = make_plot
        self.step_ms = step_ms
        self.clock = FakeClock()
        if driver_factory is None:
            self.driver = FakeDriver(clock=self.clock)
        else:
            self.driver = driver_factory(clock=self.clock)
        self.fsm = ControlFSM(self.driver, analyzer=analyzer)
        self.analyzer = analyzer
        self.samples = [(self.clock.now_ms(), self.driver.pressure_mmHg, self.fsm.state)]
        self.injections = []
        self.notes = []
        self.steps = 0
        self.expected = ""
        self.failures = []

    def fail(self, message):
        self.failures.append(message)

    def check(self, condition, message):
        if not condition:
            self.fail(message)
        return bool(condition)

    def record_injection(self, label):
        self.injections.append(
            {
                "t_ms": self.clock.now_ms(),
                "state": self.fsm.state,
                "p_mmHg": self.driver.pressure_mmHg,
                "label": label,
            }
        )

    def step(self, advance_ms=None):
        self.fsm.step()
        self.steps += 1
        self.samples.append(
            (self.clock.now_ms(), self.driver.pressure_mmHg, self.fsm.state)
        )
        if advance_ms is None:
            advance_ms = self.step_ms
        if advance_ms:
            self.clock.advance(advance_ms)

    def run_until(self, predicate, max_steps=10000, step_ms=None):
        for _ in range(max_steps):
            if predicate(self.fsm):
                return True
            self.step(step_ms)
        return predicate(self.fsm)

    def run_until_state(self, state, max_steps=10000, step_ms=None):
        return self.run_until(lambda fsm: fsm.state == state, max_steps, step_ms)

    def run_until_done(self, max_steps=10000, step_ms=None):
        return self.run_until_state(config.STATE_DONE, max_steps, step_ms)

    def state_sequence(self):
        sequence = []
        for entry in self.fsm.log:
            state = entry["state"]
            if not sequence or sequence[-1] != state:
                sequence.append(state)
        return sequence

    def finish(self):
        elapsed_ms = self.clock.now_ms()
        now = self.clock.now_ms()
        if not self.samples or self.samples[-1][0] < now:
            self.samples.append((now, self.driver.pressure_mmHg, self.fsm.state))

        plot_path = None
        if self.make_plot:
            try:
                plot_path = self._render_plot()
            except Exception as exc:
                self.fail("plot rendering failed: %r" % (exc,))

        last_p = self.fsm.last_pressure
        if last_p is None:
            last_p = self.driver.pressure_mmHg
        actual = (
            "state=%s result=%s reason=%s steps=%d elapsed=%.1fms last_p=%.2f"
            % (
                self.fsm.state,
                self.fsm.result,
                self.fsm.reason,
                self.steps,
                elapsed_ms,
                last_p,
            )
        )
        if self.notes:
            actual += " | " + "; ".join(self.notes)

        return ScenarioResult(
            number=self.number,
            name=self.name,
            title=self.title,
            expected=self.expected,
            actual=actual,
            passed=not self.failures,
            failures=list(self.failures),
            steps=self.steps,
            elapsed_ms=elapsed_ms,
            plot_path=plot_path,
            timeline=list(self.fsm.log),
            samples=list(self.samples),
            injections=list(self.injections),
        )

    def _render_plot(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        times = [item[0] / 1000.0 for item in self.samples]
        pressures = [item[1] for item in self.samples]
        states = [item[2] for item in self.samples]

        fig, ax = plt.subplots(figsize=(10.0, 4.5))
        segments = []
        for i in range(len(self.samples)):
            state = states[i]
            if not segments or segments[-1][2] != state:
                segments.append([times[i], times[i], state])
            else:
                segments[-1][1] = times[i]

        present_states = []
        for start, end, state in segments:
            if end <= start:
                end = start + self.step_ms / 1000.0
            ax.axvspan(
                start,
                end,
                color=STATE_COLORS.get(state, "#cccccc"),
                alpha=0.18,
                linewidth=0,
            )
            if state not in present_states:
                present_states.append(state)

        ax.plot(times, pressures, color="#1f4e79", linewidth=1.3)
        ax.axhline(
            config.TARGET_PRESSURE_MMHG,
            color="#7f7f7f",
            linestyle=":",
            linewidth=0.9,
        )
        ax.axhline(
            config.OVERPRESSURE_FAULT_MMHG,
            color="#d62728",
            linestyle=":",
            linewidth=0.9,
        )
        ax.axhline(
            config.ANALYZE_PRESSURE_MMHG,
            color="#2ca02c",
            linestyle=":",
            linewidth=0.9,
        )

        max_pressure = max(pressures) if pressures else 0.0
        for injection in self.injections:
            x = injection["t_ms"] / 1000.0
            ax.axvline(x, color="#333333", linestyle="--", linewidth=1.0)
            ax.text(
                x,
                max_pressure * 0.92,
                injection["label"],
                rotation=90,
                fontsize=7,
                ha="center",
                va="top",
                color="#333333",
                bbox={"facecolor": "white", "alpha": 0.6, "edgecolor": "none"},
            )

        if self.fsm.result is not None:
            summary = "DONE %s / %s" % (self.fsm.result, self.fsm.reason)
        else:
            summary = "state=%s" % self.fsm.state
        ax.text(
            0.01,
            0.98,
            summary,
            transform=ax.transAxes,
            va="top",
            fontsize=9,
            bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "#666666"},
        )

        handles = [
            Patch(
                facecolor=STATE_COLORS.get(state, "#cccccc"),
                alpha=0.35,
                label=state,
            )
            for state in present_states
        ]
        handles.extend(
            [
                Line2D([], [], color="#1f4e79", label="cuff pressure"),
                Line2D([], [], color="#7f7f7f", linestyle=":", label="target"),
                Line2D([], [], color="#d62728", linestyle=":", label="overpressure"),
                Line2D([], [], color="#2ca02c", linestyle=":", label="analyze line"),
            ]
        )
        ax.legend(handles=handles, loc="upper right", fontsize=7, ncol=2)
        ax.set_xlabel("time [s]")
        ax.set_ylabel("cuff pressure [mmHg]")
        ax.set_ylim(bottom=0.0)
        ax.grid(True, alpha=0.25)
        ax.set_title("Scenario %d: %s" % (self.number, self.title))
        fig.tight_layout()

        path = self.output_dir / ("scenario_%d_%s.png" % (self.number, self.name))
        fig.savefig(str(path), dpi=120)
        plt.close(fig)
        return str(path)


def _contains_sequence(actual, expected):
    index = 0
    for item in actual:
        if index < len(expected) and item == expected[index]:
            index += 1
    return index == len(expected)


def scenario_1_normal_full_flow(output_dir=None, make_plot=True, step_ms=12):
    analyzer = PipelineAnalyzer()
    run = ScenarioRun(
        1,
        "normal_full_flow",
        "Normal full flow with real firmware analyzer and PC reference",
        analyzer,
        output_dir=output_dir,
        make_plot=make_plot,
        step_ms=step_ms,
        driver_factory=PulsatileFakeDriver,
    )
    run.expected = (
        "DONE(OK); real firmware analyzer SBP/DBP match PC <=0.5 mmHg"
    )
    try:
        run.record_injection("START")
        run.fsm.start()
        run.check(
            run.run_until_done(max_steps=8000),
            "did not reach DONE within 8000 steps",
        )

        data = run.fsm.result_data or {}
        sbp = data.get("sbp")
        dbp = data.get("dbp")
        map_pressure = data.get("map")
        hr = data.get("hr")
        numeric = (
            isinstance(sbp, (int, float))
            and isinstance(dbp, (int, float))
            and isinstance(map_pressure, (int, float))
            and isinstance(hr, (int, float))
        )
        plausible = (
            numeric
            and 40.0 <= dbp < map_pressure < sbp <= 220.0
            and 30.0 <= hr <= 200.0
        )
        run.check(run.fsm.state == config.STATE_DONE, "final state is not DONE")
        run.check(
            run.fsm.result == config.RESULT_OK,
            "final result is not OK: %r" % (run.fsm.result,),
        )
        run.check(
            run.fsm.reason == config.REASON_NONE,
            "final reason is not NONE: %r" % (run.fsm.reason,),
        )
        run.check(analyzer.start_calls == 1, "analyzer start not called once")
        run.check(
            analyzer.step_calls > 1,
            "analyzer did not advance through multiple stages",
        )
        run.check(plausible, "firmware SBP/DBP/MAP/HR not plausible: %r" % (data,))

        run.check(
            _contains_sequence(
                run.state_sequence(),
                [
                    config.STATE_SELF_CHECK,
                    config.STATE_INFLATE,
                    config.STATE_SETTLE,
                    config.STATE_DEFLATE,
                    config.STATE_DUMP,
                    config.STATE_ANALYZE,
                    config.STATE_DONE,
                ],
            ),
            "state sequence does not contain the expected normal path",
        )
        run.check(
            len(run.fsm.buffer) > 1000,
            "DEMO buffer too small: %d" % len(run.fsm.buffer),
        )
        run.check(
            not run.driver.pump_on and not run.driver.valve_energized,
            "pump/valve are not off at DONE",
        )
        run.check(
            run.fsm.last_pressure is not None
            and run.fsm.last_pressure < config.ANALYZE_PRESSURE_MMHG,
            "final pressure is not below analyze threshold",
        )

        pc_result = pc_reference_on_buffer(run.fsm.buffer)
        pc_sbp = pc_result.decision.sbp
        pc_dbp = pc_result.decision.dbp
        run.check(
            pc_result.accepted,
            "PC reference rejected the same buffer: %r"
            % (pc_result.qc.reasons,),
        )
        run.check(
            pc_sbp is not None and pc_dbp is not None,
            "PC reference did not produce SBP/DBP",
        )
        if numeric and pc_sbp is not None and pc_dbp is not None:
            d_sbp = abs(sbp - pc_sbp)
            d_dbp = abs(dbp - pc_dbp)
            run.check(
                d_sbp <= 0.5,
                "firmware/PC SBP mismatch %.6f > 0.5" % d_sbp,
            )
            run.check(
                d_dbp <= 0.5,
                "firmware/PC DBP mismatch %.6f > 0.5" % d_dbp,
            )
            run.notes.append(
                "firmware SBP=%.2f DBP=%.2f; PC SBP=%.2f DBP=%.2f; "
                "max diff=%.3e mmHg"
                % (sbp, dbp, pc_sbp, pc_dbp, max(d_sbp, d_dbp))
            )
    except Exception as exc:
        run.fail("unexpected exception: %r" % (exc,))
    return run.finish()


def scenario_2_stop_during_deflate(output_dir=None, make_plot=True, step_ms=12):
    analyzer = RecordingAnalyzer()
    run = ScenarioRun(
        2,
        "stop_during_deflate",
        "STOP during DEFLATE: safe state, DUMP, CANCEL, no ANALYZE",
        analyzer,
        output_dir=output_dir,
        make_plot=make_plot,
        step_ms=step_ms,
    )
    run.expected = "DONE(CANCEL, USER_CANCEL); DUMP path; analyzer not called"
    try:
        run.fsm.start()
        reached = run.run_until(
            lambda fsm: fsm.state == config.STATE_DEFLATE and len(fsm.buffer) >= 100,
            max_steps=2000,
        )
        run.check(reached, "did not reach DEFLATE with buffered samples")
        buffered = len(run.fsm.buffer)

        run.record_injection("STOP")
        run.fsm.stop()
        run.step(advance_ms=0)

        run.check(
            run.fsm.state == config.STATE_DUMP,
            "STOP did not enter DUMP: %r" % (run.fsm.state,),
        )
        run.check(
            not run.driver.pump_on and not run.driver.valve_energized,
            "pump/valve not safely off after STOP",
        )
        run.check(len(run.fsm.buffer) == buffered, "buffer changed on STOP")
        run.check(analyzer.calls == [], "analyzer was called on STOP path")

        run.check(
            run.run_until_done(max_steps=5000),
            "did not reach DONE after STOP",
        )
        run.check(
            run.fsm.result == config.RESULT_CANCEL,
            "result is not CANCEL: %r" % (run.fsm.result,),
        )
        run.check(
            run.fsm.reason == config.REASON_USER_CANCEL,
            "reason is not USER_CANCEL: %r" % (run.fsm.reason,),
        )
        run.check(analyzer.calls == [], "analyzer was called after STOP")
        run.check(
            config.STATE_ANALYZE not in run.state_sequence(),
            "STOP path entered ANALYZE",
        )
        run.check(
            run.fsm.last_pressure is not None
            and run.fsm.last_pressure < config.ANALYZE_PRESSURE_MMHG,
            "final pressure is not below analyze threshold",
        )
        run.check(run.steps < 5000, "STOP scenario exceeded step bound")
    except Exception as exc:
        run.fail("unexpected exception: %r" % (exc,))
    return run.finish()


def scenario_3_overpressure(output_dir=None, make_plot=True, step_ms=12):
    analyzer = RecordingAnalyzer()
    run = ScenarioRun(
        3,
        "overpressure",
        "Overpressure: P >= 260 mmHg -> safe state -> FAULT",
        analyzer,
        output_dir=output_dir,
        make_plot=make_plot,
        step_ms=step_ms,
    )
    run.expected = "DONE(FAULT, OVERPRESSURE_260); safe state applied"
    try:
        run.fsm.start()
        run.check(
            run.run_until_state(config.STATE_INFLATE, max_steps=1000),
            "did not reach INFLATE",
        )
        safe_before = run.driver.safe_state_calls
        run.driver.set_pressure(config.OVERPRESSURE_FAULT_MMHG + 5.0)
        run.record_injection("OVERPRESSURE 265 mmHg")
        run.step(advance_ms=0)

        run.check(
            run.fsm.state == config.STATE_DUMP,
            "overpressure did not enter DUMP: %r" % (run.fsm.state,),
        )
        run.check(
            run.driver.safe_state_calls > safe_before,
            "safe_state was not called on overpressure",
        )
        run.check(
            not run.driver.pump_on and not run.driver.valve_energized,
            "pump/valve not safely off after overpressure",
        )
        run.check(analyzer.calls == [], "analyzer was called on overpressure")

        run.check(run.run_until_done(max_steps=5000), "did not reach DONE")
        run.check(
            run.fsm.result == config.RESULT_FAULT,
            "overpressure result is not FAULT: %r" % (run.fsm.result,),
        )
        run.check(
            run.fsm.reason == config.REASON_OVERPRESSURE_260,
            "overpressure reason is not OVERPRESSURE_260: %r"
            % (run.fsm.reason,),
        )
        events = [entry["event"] for entry in run.fsm.log]
        run.check("OVERPRESSURE_260" in events, "OVERPRESSURE_260 event missing")
        run.check("FAULT_OVERPRESSURE_260" in events, "FAULT event missing")
        run.check(
            run.fsm.last_pressure is not None
            and run.fsm.last_pressure < config.ANALYZE_PRESSURE_MMHG,
            "final pressure is not below analyze threshold",
        )
        run.check(analyzer.calls == [], "analyzer was called after overpressure")
    except Exception as exc:
        run.fail("unexpected exception: %r" % (exc,))
    return run.finish()


def scenario_4_sensor_not_ready(output_dir=None, make_plot=True, step_ms=12):
    analyzer = RecordingAnalyzer()
    run = ScenarioRun(
        4,
        "sensor_not_ready",
        "Persistent NOT_READY during DEFLATE: timeout, no hang",
        analyzer,
        output_dir=output_dir,
        make_plot=make_plot,
        step_ms=step_ms,
    )
    run.expected = "DONE(FAULT, SENSOR_NOT_READY_TIMEOUT) within step bound"
    max_steps = 5000
    try:
        run.fsm.start()
        reached = run.run_until(
            lambda fsm: fsm.state == config.STATE_DEFLATE and len(fsm.buffer) >= 100,
            max_steps=2000,
        )
        run.check(reached, "did not reach DEFLATE with buffered samples")

        run.record_injection("Sensor NOT_READY")
        run.driver.set_sensor_status(config.STATUS_NOT_READY)
        done = run.run_until_done(max_steps=max_steps)

        run.check(done, "NOT_READY scenario did not reach DONE within step bound")
        run.check(
            run.fsm.result == config.RESULT_FAULT,
            "NOT_READY result is not FAULT: %r" % (run.fsm.result,),
        )
        run.check(
            run.fsm.reason == config.REASON_SENSOR_NOT_READY_TIMEOUT,
            "NOT_READY reason mismatch: %r" % (run.fsm.reason,),
        )
        run.check(analyzer.calls == [], "analyzer was called on NOT_READY fault")
        run.check(run.steps < max_steps, "NOT_READY scenario exceeded step bound")
        run.check(
            config.STATE_ANALYZE not in run.state_sequence(),
            "NOT_READY fault entered ANALYZE",
        )
        events = [entry["event"] for entry in run.fsm.log]
        run.check(
            "FAULT_SENSOR_NOT_READY_TIMEOUT" in events,
            "NOT_READY fault event missing",
        )
        run.check(
            not run.driver.pump_on and not run.driver.valve_energized,
            "pump/valve not safely off after NOT_READY fault",
        )
    except Exception as exc:
        run.fail("unexpected exception: %r" % (exc,))
    return run.finish()


def scenario_5_safe_state_fallback(output_dir=None, make_plot=True, step_ms=12):
    analyzer = RecordingAnalyzer()
    run = ScenarioRun(
        5,
        "safe_state_fallback",
        "Composite safe_state failure with single-channel fallback",
        analyzer,
        output_dir=output_dir,
        make_plot=make_plot,
        step_ms=step_ms,
    )
    run.expected = (
        "pump/valve off, pressure not rising, SAFE_STATE_FALLBACK_OK present"
    )
    try:
        run.fsm.start()
        run.check(
            run.run_until_state(config.STATE_INFLATE, max_steps=1000),
            "did not reach INFLATE",
        )
        for _ in range(10):
            run.step()

        run.driver.fail_safe_state = True
        run.driver.set_pressure(config.OVERPRESSURE_FAULT_MMHG + 5.0)
        run.record_injection("Overpressure + safe_state failure")
        run.step(advance_ms=0)
        injection_t = run.injections[0]["t_ms"]

        run.check(
            run.fsm.state == config.STATE_DUMP,
            "fallback fault did not enter DUMP: %r" % (run.fsm.state,),
        )
        run.check(
            not run.driver.pump_on and not run.driver.valve_energized,
            "fallback did not close pump/valve",
        )
        run.check(run.fsm.is_healthy() is True, "fallback left controller unhealthy")
        run.check(run.fsm.safe_state_errors >= 1, "safe_state error was not counted")
        events = [entry["event"] for entry in run.fsm.log]
        run.check("SAFE_STATE_FALLBACK_OK" in events, "fallback event missing")
        run.check("SAFE_STATE_ERROR" in events, "safe_state error event missing")
        run.check(
            run.driver.pressure_mmHg <= config.OVERPRESSURE_FAULT_MMHG + 5.01,
            "pressure rose immediately after fallback",
        )

        run.check(run.run_until_done(max_steps=5000), "fallback did not reach DONE")
        run.check(
            run.fsm.result == config.RESULT_FAULT,
            "fallback final result is not FAULT: %r" % (run.fsm.result,),
        )
        run.check(
            run.fsm.reason == config.REASON_OVERPRESSURE_260,
            "fallback lost overpressure reason: %r" % (run.fsm.reason,),
        )
        after = [
            pressure
            for t_ms, pressure, _state in run.samples
            if t_ms >= injection_t
        ]
        run.check(
            not after or max(after) <= config.OVERPRESSURE_FAULT_MMHG + 5.01,
            "pressure continued rising after fallback: max=%r" % (after,),
        )
        run.check(
            run.fsm.last_pressure is not None
            and run.fsm.last_pressure < config.ANALYZE_PRESSURE_MMHG,
            "final pressure is not below analyze threshold",
        )
        run.check(analyzer.calls == [], "analyzer was called on fallback fault")
    except Exception as exc:
        run.fail("unexpected exception: %r" % (exc,))
    return run.finish()


SCENARIOS = (
    scenario_1_normal_full_flow,
    scenario_2_stop_during_deflate,
    scenario_3_overpressure,
    scenario_4_sensor_not_ready,
    scenario_5_safe_state_fallback,
)


def _print_timeline(result):
    print(
        "%8s | %-10s | %8s | %-4s | %-5s | %s"
        % ("t_ms", "state", "p_mmHg", "pump", "valve", "event")
    )
    print("-" * 88)
    if not result.timeline:
        print("(no log entries)")
        return
    for entry in result.timeline:
        p_mmHg = entry.get("p_mmHg")
        p_text = "-" if p_mmHg is None else "%.1f" % p_mmHg
        print(
            "%8s | %-10s | %8s | %-4s | %-5s | %s"
            % (
                entry.get("t_ms"),
                entry.get("state"),
                p_text,
                "ON" if entry.get("pump") else "off",
                "ON" if entry.get("valve") else "off",
                entry.get("event"),
            )
        )


def _print_result(result):
    print("\n" + "=" * 100)
    print("Scenario %d: %s" % (result.number, result.title))
    _print_timeline(result)
    for injection in result.injections:
        print(
            "  [inject] t=%s ms state=%s p=%.2f label=%s"
            % (
                injection["t_ms"],
                injection["state"],
                injection["p_mmHg"],
                injection["label"],
            )
        )
    status = "PASS" if result.passed else "FAIL"
    print("%s: expected=%s | actual=%s" % (status, result.expected, result.actual))
    if not result.passed:
        for failure in result.failures:
            print("  - %s" % failure)
    if result.plot_path is not None:
        print("  plot: %s" % result.plot_path)


def run_all(output_dir=None, make_plot=True):
    results = []
    for function in SCENARIOS:
        try:
            result = function(output_dir=output_dir, make_plot=make_plot)
        except Exception as exc:
            result = ScenarioResult(
                number=len(results) + 1,
                name=function.__name__,
                title=function.__name__,
                expected="scenario completes",
                actual="unexpected exception: %r" % (exc,),
                passed=False,
                failures=["unexpected exception: %r" % (exc,)],
                steps=0,
                elapsed_ms=0,
                plot_path=None,
                timeline=[],
                samples=[],
                injections=[],
            )
        results.append(result)
        _print_result(result)
    return results


def main():
    output_dir = DEFAULT_OUTPUT_DIR
    results = run_all(output_dir=output_dir, make_plot=True)
    passed = sum(1 for result in results if result.passed)
    total = len(results)
    print("\n" + "=" * 100)
    print("%d/%d scenarios passed" % (passed, total))
    for result in results:
        print(
            "  %s scenario_%d_%s"
            % (
                "PASS" if result.passed else "FAIL",
                result.number,
                result.name,
            )
        )
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
