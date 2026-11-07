"""定长 DEFLATE 样本缓冲（MicroPython 友好）。

内部只保留两个 ``array('H')``：
- ``seq`` 不再存数组，记录 ``first_seq``，按索引推导 ``first_seq + i``；
- ``t_ms`` 存相邻样本的 16-bit 增量（首样本为 0），
  绝对时间 = ``t_origin_ms + 累计增量``；12.5 ms 级间隔远小于 65535；
- 压力存无符号 16-bit centi-mmHg（0–655.35 mmHg），负压（传感器零漂）
  钳到 0；280 mmHg 超压 = 28000，量程充足。

``get()`` / ``__iter__()`` 对外仍返回 ``(seq, 绝对 t_ms, mmHg)``；
``append/clear/is_full/len`` 契约不变。``t_ms`` 非单调或增量超过
65535 ms 时 ``append`` 抛 ``ValueError``，由控制层转 BUFFER_OVERFLOW。
缓冲不使用逐样本元组列表。
"""

from array import array

# MicroPython 文档未承诺 array.itemsize；RP2350 的 'H' 为 2 字节。
_H_ITEMSIZE = 2
try:
    _H_ITEMSIZE = array("H").itemsize
except AttributeError:
    pass

_MAX_CENTIMMHG = 65535
_MAX_DELTA_MS = 65535


def _mmhg_to_centimmHg(p_mmHg):
    scaled = p_mmHg * 100.0
    if scaled >= 0.0:
        value = int(scaled + 0.5)
    else:
        value = int(scaled - 0.5)
    if value < 0:
        return 0
    if value > _MAX_CENTIMMHG:
        return _MAX_CENTIMMHG
    return value


class DeflateBuffer(object):
    """固定容量、满即拒绝追加的 DEFLATE 缓冲。"""

    def __init__(self, max_samples):
        max_samples = int(max_samples)
        if max_samples <= 0:
            raise ValueError("DeflateBuffer 容量必须为正")
        self.max_samples = max_samples
        # MicroPython 的 array 不支持 array * int；用零字节缓冲构造定长数组。
        half_bytes = _H_ITEMSIZE * max_samples
        self.t_ms = array("H", bytes(half_bytes))
        self.pressure_centimmHg = array("H", bytes(half_bytes))
        self.t_origin_ms = None
        self.first_seq = None
        self._last_t_ms = None
        self._len = 0

    def __len__(self):
        return self._len

    def is_full(self):
        return self._len >= self.max_samples

    def clear(self):
        self._len = 0
        self.t_origin_ms = None
        self.first_seq = None
        self._last_t_ms = None

    def append(self, seq, t_ms, p_mmHg):
        """追加一个有效样本；容量已满时返回 False，不写入。"""
        if self._len >= self.max_samples:
            return False
        i = self._len
        t_ms = int(t_ms)
        if i == 0:
            self.t_origin_ms = t_ms
            self.first_seq = int(seq)
            delta = 0
        else:
            delta = t_ms - self._last_t_ms
            if delta < 0:
                raise ValueError("t_ms 必须单调不减")
            if delta > _MAX_DELTA_MS:
                raise ValueError("t_ms 增量超过 65535 ms")
        self.t_ms[i] = delta
        self.pressure_centimmHg[i] = _mmhg_to_centimmHg(float(p_mmHg))
        self._last_t_ms = t_ms
        self._len += 1
        return True

    def _t_rel(self, i):
        total = 0
        for k in range(1, i + 1):
            total += self.t_ms[k]
        return total

    def t_ms_at(self, i):
        """返回第 i 个样本的绝对毫秒时间。"""
        if i < 0:
            i += self._len
        if i < 0 or i >= self._len:
            raise IndexError("DeflateBuffer index")
        origin = self.t_origin_ms
        if origin is None:
            origin = 0
        return origin + self._t_rel(i)

    def get(self, i):
        """返回 ``(seq, t_ms, p_mmHg)``；t_ms 为绝对毫秒。"""
        if i < 0:
            i += self._len
        if i < 0 or i >= self._len:
            raise IndexError("DeflateBuffer index")
        return (
            self.first_seq + i,
            self.t_ms_at(i),
            self.pressure_centimmHg[i] / 100.0,
        )

    def __getitem__(self, i):
        return self.get(i)

    def __iter__(self):
        origin = self.t_origin_ms
        if origin is None:
            origin = 0
        first = self.first_seq
        t_rel = 0
        for i in range(self._len):
            if i:
                t_rel += self.t_ms[i]
            yield (
                first + i,
                origin + t_rel,
                self.pressure_centimmHg[i] / 100.0,
            )

