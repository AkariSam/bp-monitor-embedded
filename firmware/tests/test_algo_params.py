"""参数移植一致性：固件 AlgoParams 与 PC AlgoParams 默认值逐项相同。"""

import pytest

from algo.params import AlgoParams as FirmwareParams
from bp.params import AlgoParams as PcParams

FIELDS = (
    "fs",
    "dc_order",
    "dc_cut_hz",
    "ac_hp_order",
    "ac_hp_hz",
    "ac_lp_order",
    "ac_lp_hz",
    "gd_ac_ref_hz",
    "gd_dc_ref_hz",
    "edge_margin_s",
    "grad_block_s",
    "grad_factor",
    "grad_min",
    "prom_ratio",
    "prom_ref_n",
    "prom_arm_n",
    "rr_min_s",
    "rr_max_s",
    "lim_dev",
    "lim_nbr",
    "lim_nbr_min",
    "env_median",
    "env_ma",
    "env_min_points",
    "ratio_sbp",
    "ratio_dbp",
    "dia_tol",
    "ipp_thr",
    "ipp_ratio_thr",
    "hr_min",
    "hr_max",
    "env_cv_max",
    "outlier_ratio_max",
)


def test_defaults_match_pc_source():
    fw = FirmwareParams()
    pc = PcParams()
    for name in FIELDS:
        assert getattr(fw, name) == getattr(pc, name), name


def test_keyword_override_and_unknown_parameter():
    p = FirmwareParams(fs=40.0, ratio_sbp=0.55)
    assert p.fs == 40.0
    assert p.ratio_sbp == 0.55
    with pytest.raises(TypeError):
        FirmwareParams(not_a_param=1)
