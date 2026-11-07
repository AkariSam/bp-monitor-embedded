"""DEFLATE 轻量缓冲：array 存储、容量上限与接近 80 Hz 集成。"""

from array import array

import pytest

import config
from buffer import DeflateBuffer


class RaisingBuffer(DeflateBuffer):
    def append(self, seq, t_ms, p_mmHg):
        raise OverflowError("forced append failure")


def test_deflate_buffer_uses_array_and_is_bounded():
    buf = DeflateBuffer(3)
    assert isinstance(buf.t_ms, array)
    assert isinstance(buf.pressure_centimmHg, array)
    assert buf.t_ms.typecode == "H"
    assert buf.pressure_centimmHg.typecode == "H"
    assert len(buf.t_ms) == 3
    assert len(buf.pressure_centimmHg) == 3
    assert len(buf) == 0
    assert buf.first_seq is None

    assert buf.append(10, 100, 123.45) is True
    assert buf.append(11, 112, 123.30) is True
    assert buf.append(12, 124, 123.10) is True
    assert buf.is_full()
    assert buf.append(13, 136, 122.90) is False
    assert len(buf) == 3

    seq, t_ms, p_mmHg = buf[0]
    assert seq == 10
    assert t_ms == 100
    assert abs(p_mmHg - 123.45) < 1e-9
    assert buf[2][0] == 12
    assert [item[0] for item in buf] == [10, 11, 12]
    assert buf.first_seq == 10
    assert buf.t_origin_ms == 100
    assert buf.t_ms[0] == 0
    assert buf.t_ms[1] == 12
    assert buf.t_ms[2] == 12
    assert [item[1] for item in buf] == [100, 112, 124]

    buf.clear()
    assert len(buf) == 0
    assert buf.t_origin_ms is None
    assert buf.first_seq is None
    assert buf.append(20, 200, -0.4) is True
    assert buf.append(21, 212, 280.0) is True
    assert buf.append(22, 224, 700.0) is True
    assert buf.t_origin_ms == 200
    assert buf.first_seq == 20
    assert buf.t_ms[0] == 0
    assert buf.pressure_centimmHg[0] == 0
    assert buf.pressure_centimmHg[1] == 28000
    assert buf.pressure_centimmHg[2] == 65535
    assert buf[0][2] == 0.0
    assert abs(buf[1][2] - 280.0) < 1e-9


def test_buffer_t_origin_near_int32_boundary():
    base = 2 ** 31 - 20
    buf = DeflateBuffer(4)
    assert buf.append(1, base, 120.0) is True
    assert buf.append(2, base + 12, 119.9) is True
    assert buf.append(3, base + 24, 119.8) is True

    assert buf.t_origin_ms == base
    assert list(buf.t_ms)[:3] == [0, 12, 12]
    assert [buf[i][1] for i in range(3)] == [base, base + 12, base + 24]


def test_near_80hz_normal_flow_uses_buffer_object(make_bench):
    seen = {}

    def analyzer(buffer):
        seen["is_buffer"] = isinstance(buffer, DeflateBuffer)
        seen["first_seq"] = buffer.first_seq
        seen["typecode"] = buffer.t_ms.typecode
        seen["len"] = len(buffer)
        return (config.RESULT_OK, config.REASON_NONE, None)

    bench = make_bench(analyzer=analyzer)
    bench.fsm.start()
    assert bench.run_until_done(step_ms=12, max_ms=70000)

    assert bench.fsm.result == config.RESULT_OK
    assert seen["is_buffer"] is True
    assert isinstance(seen["first_seq"], int)
    assert seen["typecode"] == "H"
    assert seen["len"] > 2000
    assert isinstance(bench.fsm.buffer, DeflateBuffer)
    assert not isinstance(bench.fsm.buffer, list)
    assert len(bench.fsm.buffer) > 2000


def test_buffer_overflow_faults_without_analyzing(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer, max_deflate_samples=20)
    bench.fsm.start()
    assert bench.run_until_done(max_ms=20000)

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_BUFFER_OVERFLOW
    assert len(bench.fsm.buffer) == 20
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert analyzer.calls == []


def test_80hz_sensor_faster_control_loop_dedups_seq(make_bench):
    seen = {}

    def analyzer(buffer):
        seen["len"] = len(buffer)
        return (config.RESULT_OK, config.REASON_NONE, None)

    # 控制步 3 ms，传感器每 4 次读取才产生一个新样本 → 有效采样约 12 ms。
    bench = make_bench(analyzer=analyzer, reads_per_sample=4)
    bench.fsm.start()
    assert bench.run_until_done(step_ms=3, max_ms=70000)

    assert bench.fsm.result == config.RESULT_OK
    assert seen["len"] == len(bench.fsm.buffer)
    assert len(bench.fsm.buffer) > 2000
    assert len(bench.fsm.buffer) < 5000
    assert bench.driver.pressure_read_calls > len(bench.fsm.buffer) * 2

    seqs = [item[0] for item in bench.fsm.buffer]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_append_exception_becomes_buffer_overflow_fault(make_bench, analyzer):
    bench = make_bench(analyzer=analyzer)
    bench.fsm.buffer = RaisingBuffer(64)
    bench.fsm.start()
    assert bench.run_until_done(max_ms=20000)

    assert bench.fsm.state == config.STATE_DONE
    assert bench.fsm.result == config.RESULT_FAULT
    assert bench.fsm.reason == config.REASON_BUFFER_OVERFLOW
    assert len(bench.fsm.buffer) == 0
    assert bench.driver.pump_on is False
    assert bench.driver.valve_energized is False
    assert analyzer.calls == []


def test_buffer_rejects_non_monotonic_time():
    buf = DeflateBuffer(2)
    assert buf.append(1, 100, 120.0) is True
    with pytest.raises(ValueError):
        buf.append(2, 99, 120.0)
    assert len(buf) == 1
