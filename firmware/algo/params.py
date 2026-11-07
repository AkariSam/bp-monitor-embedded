"""固件算法参数（移植自 ``algo/bp/params.py``，单一来源）。

数值与 PC 正本默认值一致；来源为《技术实现计划 v2.2》§4.2–4.4。
本模块不使用 dataclasses/typing，保持 MicroPython 可导入；支持关键字覆盖
单项参数，供测试或后续 Flash 参数表使用。
"""


class AlgoParams(object):
    def __init__(self, **kwargs):
        # ---- 滤波器（§4.2）----
        self.fs = 80.0
        self.dc_order = 2
        self.dc_cut_hz = 0.5
        self.ac_hp_order = 2
        self.ac_hp_hz = 0.5
        self.ac_lp_order = 2
        self.ac_lp_hz = 8.0
        self.gd_ac_ref_hz = 1.2
        self.gd_dc_ref_hz = 0.1
        self.edge_margin_s = 0.5

        # ---- 峰检测（§4.3）----
        self.grad_block_s = 1.25
        self.grad_factor = 1.0
        self.grad_min = 0.5
        self.prom_ratio = 0.25
        self.prom_ref_n = 10
        self.prom_arm_n = 3
        self.rr_min_s = 0.3
        self.rr_max_s = 1.5
        self.lim_dev = 0.5
        self.lim_nbr = 1
        self.lim_nbr_min = 2

        # ---- 包络（§4.3）----
        self.env_median = 3
        self.env_ma = 3
        self.env_min_points = 8

        # ---- 判定（§4.4）----
        self.ratio_sbp = 0.50
        self.ratio_dbp = 0.70
        self.dia_tol = 10.0
        self.ipp_thr = 0.2
        self.ipp_ratio_thr = 0.2
        self.hr_min = 40.0
        self.hr_max = 180.0

        # ---- 质控（§4.4）----
        self.env_cv_max = 0.35
        self.outlier_ratio_max = 0.3

        for key, value in kwargs.items():
            if not hasattr(self, key):
                raise TypeError("未知参数: %s" % key)
            setattr(self, key, value)
