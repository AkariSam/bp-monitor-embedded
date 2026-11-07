"""演示场景的 pytest 集成：每个场景都必须 PASS 并生成 PNG。"""

from pathlib import Path

import pytest

from demo import run_demo


@pytest.mark.parametrize(
    "scenario",
    run_demo.SCENARIOS,
    ids=[scenario.__name__ for scenario in run_demo.SCENARIOS],
)
def test_demo_scenario_passes(scenario, tmp_path):
    result = scenario(output_dir=tmp_path, make_plot=True)

    assert result.passed, result.failure_text()
    assert result.steps > 0
    assert result.timeline
    assert result.plot_path is not None
    assert Path(result.plot_path).exists()
