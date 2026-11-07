"""float32 量化注入：把固件算法模块的中间量往返到 IEEE-754 binary32。

用法::

    with Float32Hooks():
        result = run_firmware(buffer)

钩子默认关闭；``f32`` 只做一次单精度往返，结果仍存于 Python float64。
这不是逐指令的单精度乘加模拟，而是对滤波器状态/系数、延迟估计、包络、
判定与 QC 算术做“每步单精度舍入”的敏感性压力测试。
"""

import struct

from algo import decision, envelope, filters, peaks, qc

_MODULES = (filters, peaks, envelope, decision, qc)


def f32(value):
    """float64 → IEEE-754 binary32 → float64。"""
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def f32_array(values):
    return [f32(v) for v in values]


class Float32Hooks(object):
    """上下文管理器：进入时给五个算法模块装 quantizer，退出时卸载。"""

    def __enter__(self):
        for module in _MODULES:
            module.set_quantizer(f32)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        for module in _MODULES:
            module.set_quantizer(None)
        return False
