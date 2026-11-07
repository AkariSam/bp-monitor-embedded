"""pytest 引导与 FakeDriver 测试台。

只把 ``firmware/`` 加入 sys.path，保持与设备上 ``import config`` /
``from drivers import ...`` 相同的扁平导入方式。
"""

import sys
from pathlib import Path

FIRMWARE_DIR = Path(__file__).resolve().parents[1]
if str(FIRMWARE_DIR) not in sys.path:
    sys.path.insert(0, str(FIRMWARE_DIR))

ALGO_DIR = Path(__file__).resolve().parents[2] / "algo"
if str(ALGO_DIR) not in sys.path:
    sys.path.append(str(ALGO_DIR))

import pytest

import config
from buffer import DeflateBuffer
from control_fsm import ControlFSM
from drivers.fake_driver import FakeClock, FakeDriver


def deflate_buffer_from_recording(rec, max_samples=None):
    """从 PC Recording 的 DEFLATE 段构造固件 DeflateBuffer。"""
    mask = rec.phase == "DEFLATE"
    t_values = rec.t_ms[mask]
    p_values = rec.p_raw[mask]
    count = len(t_values)
    if max_samples is not None and count > max_samples:
        count = max_samples
    buffer = DeflateBuffer(max(1, count))
    for i in range(count):
        if not buffer.append(
            i,
            int(round(float(t_values[i]))),
            float(p_values[i]),
        ):
            raise AssertionError("DeflateBuffer append failed")
    return buffer


class RecordingAnalyzer(object):
    """记录每次调用收到的 DEFLATE 缓冲。"""

    def __init__(
        self,
        result=config.RESULT_OK,
        reason=config.REASON_NONE,
        data=None,
        as_string=False,
    ):
        self.result = result
        self.reason = reason
        self.data = data
        self.as_string = as_string
        self.calls = []

    def __call__(self, buffer):
        self.calls.append(list(buffer))
        if self.as_string:
            return self.result
        return (self.result, self.reason, self.data)


class Bench(object):
    """FakeClock + FakeDriver + ControlFSM 的组合测试台。"""

    def __init__(self, analyzer=None, analyze_deflate=None, max_deflate_samples=None, **driver_kwargs):
        self.clock = FakeClock()
        self.driver = FakeDriver(clock=self.clock, **driver_kwargs)
        self.fsm = ControlFSM(
            self.driver,
            analyzer=analyzer,
            analyze_deflate=analyze_deflate,
            max_deflate_samples=max_deflate_samples,
        )

    def step(self, advance_ms=0):
        self.fsm.step()
        if advance_ms:
            self.clock.advance(advance_ms)

    def run_until(self, predicate, max_ms=130000, step_ms=100):
        elapsed = 0
        while True:
            self.fsm.step()
            if predicate(self.fsm):
                return True
            if elapsed >= max_ms:
                return False
            self.clock.advance(step_ms)
            elapsed += step_ms

    def run_until_state(self, state, max_ms=130000, step_ms=100):
        return self.run_until(lambda fsm: fsm.state == state, max_ms, step_ms)

    def run_until_done(self, max_ms=130000, step_ms=100):
        return self.run_until_state(config.STATE_DONE, max_ms, step_ms)

    def advance(self, ms):
        self.clock.advance(ms)


@pytest.fixture
def make_bench():
    def _make(**kwargs):
        return Bench(**kwargs)

    return _make


@pytest.fixture
def analyzer():
    return RecordingAnalyzer()


@pytest.fixture
def make_deflate_buffer():
    return deflate_buffer_from_recording


@pytest.fixture(scope="session")
def pc_case_loader():
    from bp.csvio import read_csv
    from bp.pipeline import run

    def _load(name):
        rec = read_csv(str(ALGO_DIR / "data" / "sim" / (name + ".csv")))
        return rec, run(rec)

    return _load


@pytest.fixture(scope="session")
def pc_normal_case(pc_case_loader):
    return pc_case_loader("normal_s001")
