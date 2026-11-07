"""包络构建与平滑的单元测试。"""

import numpy as np

from bp.envelope import build, from_peaks, smooth


def test_build_sorts_by_pressure():
    pc = np.array([120.0, 80.0, 100.0, 60.0])
    amp = np.array([1.0, 2.0, 3.0, 0.5])
    env = build(pc, amp)
    assert np.all(np.diff(env.pc) > 0)
    assert np.array_equal(env.pc, [60.0, 80.0, 100.0, 120.0])
    assert np.array_equal(env.amp, [0.5, 2.0, 3.0, 1.0])


def test_smooth_removes_single_spike():
    amp = np.ones(11)
    amp[5] = 10.0
    y = smooth(amp)
    assert abs(y[5] - 1.0) < 0.4
    assert np.allclose(np.delete(y, 5), 1.0, atol=0.2)


def test_smooth_constant_preserved():
    amp = np.full(20, 0.7)
    assert np.allclose(smooth(amp), 0.7)


def test_smooth_empty():
    assert smooth(np.array([])).size == 0


def test_build_n_points():
    env = build(np.arange(60.0, 100.0, 4.0), np.linspace(0.5, 2.0, 10))
    assert env.n_points == 10
    assert env.amp_s.shape == env.amp.shape


def test_from_peaks_windows():
    from bp.peaks import Candidate

    n = 400
    dc = np.full(n, 100.0)
    t = np.arange(n) / 80.0
    ac = 2.0 * np.sin(2 * np.pi * 1.0 * t)
    peaks = [Candidate(i, i / 80.0, 2.0, 2.0, True, "")
             for i in (20, 100, 180, 260, 340)]
    pc, amp = from_peaks(dc, ac, peaks)
    assert pc.size == 5
    assert np.allclose(pc, 100.0)
    assert np.all(amp > 3.5)  # ptp ≈ 2×2.0


def test_from_peaks_empty():
    pc, amp = from_peaks(np.zeros(10), np.zeros(10), [])
    assert pc.size == 0 and amp.size == 0