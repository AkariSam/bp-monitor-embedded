"""sim/artifacts.py 单元测试：量化、噪声、呼吸、开关恒等性。"""

import numpy as np

from sim.artifacts import (ArtifactParams, add_breathing, add_noise,
                           apply_artifacts, quantize)
from sim.model import PhysioParams, synthesize


def test_quantize_to_lsb_multiples():
    p = np.random.default_rng(0).uniform(0, 200, 5000)
    q = quantize(p, 0.1)
    assert np.allclose(q * 10, np.round(q * 10), atol=1e-9)
    assert np.max(np.abs(q - p)) <= 0.05 + 1e-9


def test_quantize_identity_on_aligned_values():
    p = np.round(np.linspace(0, 10, 101) * 10) / 10
    # 对齐值经量化后至多相差 1 ulp（二进制浮点表示误差，%.2f 输出无影响）
    assert np.allclose(quantize(p, 0.1), p, atol=1e-9, rtol=0.0)


def test_noise_std_matches_sigma():
    out = add_noise(np.zeros(200_000), 0.05, np.random.default_rng(0))
    assert abs(out.std() - 0.05) < 0.05 * 0.2


def test_breathing_amplitude_and_frequency():
    fs = 1000.0
    t = np.arange(int(20 * fs)) / fs
    out = add_breathing(np.zeros_like(t), t, amp=0.5, hz=0.25,
                        rng=np.random.default_rng(0))
    assert abs(out.max() - 0.5) < 0.02
    spec = np.abs(np.fft.rfft(out))
    freqs = np.fft.rfftfreq(out.size, 1 / fs)
    assert abs(freqs[np.argmax(spec)] - 0.25) < 0.06


def test_apply_artifacts_noop_when_disabled():
    sig = synthesize(PhysioParams(), rng=np.random.default_rng(0))
    params = ArtifactParams(noise_sigma=0.0, breathing_amp=0.0)
    p = apply_artifacts(sig.p_clean, sig.t, params,
                        rng_noise=np.random.default_rng(1),
                        rng_breath=np.random.default_rng(2))
    assert np.array_equal(p, sig.p_clean)
