"""非理想因素注入（v1）：传感器白噪声、呼吸调制、工频开关、量化。

运动伪影与泵阀瞬态为「已识别但未建模」项，清单与补做计划见
``docs/sim/artifacts.md``。
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ArtifactParams:
    """非理想因素参数；默认值 = normal 基线（温和强度）。"""

    noise_sigma: float = 0.02   # 传感器白噪声 σ [mmHg]
    breathing_amp: float = 0.5  # 呼吸调制幅度 [mmHg]
    breathing_hz: float = 0.25  # 呼吸频率 [Hz]（15 次/分）
    mains_on: bool = False      # 工频干扰开关（默认关；50 Hz 在 80 Hz 下混叠至 30 Hz）
    mains_amp: float = 0.2      # 工频幅度 [mmHg]
    mains_hz: float = 50.0      # 工频频率 [Hz]


def add_noise(p: np.ndarray, sigma: float,
              rng: np.random.Generator) -> np.ndarray:
    """加性白高斯噪声（在密集网格上加 → 抽样时自然混叠，更接近真实 ADC）。"""
    return p + sigma * rng.standard_normal(p.shape)


def add_breathing(p: np.ndarray, t: np.ndarray, amp: float, hz: float,
                  rng: np.random.Generator) -> np.ndarray:
    """呼吸调制：正弦叠加（初相随机）；频率骑在 0.5 Hz 高通边界上。"""
    phase0 = rng.uniform(0.0, 2.0 * np.pi)
    return p + amp * np.sin(2.0 * np.pi * hz * t + phase0)


def add_mains(p: np.ndarray, t: np.ndarray, amp: float,
              hz: float) -> np.ndarray:
    """工频干扰（默认关闭）。"""
    return p + amp * np.sin(2.0 * np.pi * hz * t)


def quantize(p: np.ndarray, lsb: float) -> np.ndarray:
    """ADC 量化：舍入到 LSB 整数倍（在输出网格上执行）。"""
    return np.round(np.asarray(p, dtype=float) / lsb) * lsb


def apply_artifacts(p_clean: np.ndarray, t: np.ndarray,
                    params: ArtifactParams,
                    rng_noise: np.random.Generator,
                    rng_breath: np.random.Generator) -> np.ndarray:
    """按固定顺序注入噪声（顺序是复现契约的一部分，勿随意改动）：

    白噪声 → 呼吸 →（可选）工频。量化在采样之后单独执行。
    """
    p = add_noise(p_clean, params.noise_sigma, rng_noise)
    p = add_breathing(p, t, params.breathing_amp, params.breathing_hz,
                      rng_breath)
    if params.mains_on:
        p = add_mains(p, t, params.mains_amp, params.mains_hz)
    return p
