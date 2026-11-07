"""场景库：8 个命名场景 → (PhysioParams, ArtifactParams)。

每个场景对应的测试意图见 ``docs/sim/scenarios.md``。
"""

from dataclasses import replace

from sim.artifacts import ArtifactParams
from sim.model import PhysioParams


def normal():
    """120/80 @ 70 bpm：基线场景。"""
    return PhysioParams(), ArtifactParams()


def hypertension():
    """160/100：包络整体右移（高值端锚定/自相似验证点）。"""
    return replace(PhysioParams(), sbp=160.0, dbp=100.0), ArtifactParams()


def hypotension():
    """90/60：包络左移，低血压端比率行为。"""
    return replace(PhysioParams(), sbp=90.0, dbp=60.0), ArtifactParams()


def wide_pp():
    """150/70（脉压 80）：比率-真值敏感性场景。"""
    return replace(PhysioParams(), sbp=150.0, dbp=70.0), ArtifactParams()


def af():
    """房颤：RR 间期抖动 ±20%，考验峰有效性剔除路径。"""
    return replace(PhysioParams(), beat_jitter=0.2), ArtifactParams()


def bradycardia():
    """40 bpm：搏动稀疏，考验包络点数下限（<8 判废路径）。"""
    return replace(PhysioParams(), hr=40.0), ArtifactParams()


def tachycardia():
    """180 bpm：搏动密集，考验峰距下限（300 ms）。"""
    return replace(PhysioParams(), hr=180.0), ArtifactParams()


def truncated():
    """预充不足（105 < SBP=120）：高压侧包络被截断。"""
    return replace(PhysioParams(), p_target=105.0), ArtifactParams()


SCENARIOS = {
    "normal": normal,
    "hypertension": hypertension,
    "hypotension": hypotension,
    "wide_pp": wide_pp,
    "af": af,
    "bradycardia": bradycardia,
    "tachycardia": tachycardia,
    "truncated": truncated,
}


def get_scenario(name: str) -> tuple[PhysioParams, ArtifactParams]:
    """按名称取场景参数；未知名称抛 KeyError。"""
    if name not in SCENARIOS:
        raise KeyError(f"未知场景 {name!r}；可用：{sorted(SCENARIOS)}")
    return SCENARIOS[name]()
