"""数据契约：CSV 读写与格式校验。

契约（W5 与嵌入式侧冻结后以此文件为唯一权威）：
- 三列：``t_ms,p_raw,phase``
- ``t_ms``：浮点毫秒（0.0, 12.5, 25.0, …），严格递增
- ``p_raw``：袖带压 [mmHg]，两位小数（量化后）
- ``phase``：∈ {INFLATE, SETTLE, DEFLATE, DUMP}

本模块是整个工具链唯一读写 CSV 的地方；仿真器与固件采集器输出同一格式。
"""

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

PHASES = ("INFLATE", "SETTLE", "DEFLATE", "DUMP")
HEADER = ("t_ms", "p_raw", "phase")
DT_MS = 12.5  # 80 Hz 输出网格的采样间隔 [ms]（与 sim.model.FS_OUT 保持一致）


@dataclass(frozen=True)
class Recording:
    """一次测量的完整记录。"""

    t_ms: np.ndarray
    p_raw: np.ndarray
    phase: np.ndarray


def write_csv(path: str | Path, rec: Recording) -> None:
    """按契约写 CSV（t_ms 一位小数、p_raw 两位小数）。"""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for t, p, ph in zip(rec.t_ms, rec.p_raw, rec.phase):
            writer.writerow((f"{t:.1f}", f"{p:.2f}", str(ph)))


def read_csv(path: str | Path) -> Recording:
    """读契约 CSV → Recording；表头或行格式错误时抛 ValueError。"""
    t_ms: list[float] = []
    p_raw: list[float] = []
    phase: list[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None or tuple(header) != HEADER:
            raise ValueError(f"表头不符：期望 {HEADER}，实际 {header}")
        for lineno, row in enumerate(reader, start=2):
            if len(row) != len(HEADER):
                raise ValueError(f"第 {lineno} 行列数不符：{row}")
            t_ms.append(float(row[0]))
            p_raw.append(float(row[1]))
            phase.append(row[2])
    return Recording(
        t_ms=np.array(t_ms, dtype=float),
        p_raw=np.array(p_raw, dtype=float),
        phase=np.array(phase, dtype="<U7"),
    )


def validate(rec: Recording, dt_ms: float = DT_MS) -> list[str]:
    """校验记录是否符合契约；返回问题清单（空列表 = 通过）。

    ``dt_ms`` 可按输出采样率覆盖（如 40 Hz 用 25.0），供采样率扫描实验使用。
    """
    issues: list[str] = []
    n = len(rec.t_ms)
    if n == 0:
        return ["空记录"]
    if len(rec.p_raw) != n or len(rec.phase) != n:
        issues.append("三列长度不一致")
    if not np.all(np.isfinite(rec.t_ms)):
        issues.append("t_ms 含 NaN/inf")
    elif not np.all(np.diff(rec.t_ms) > 0):
        issues.append("t_ms 非严格递增")
    elif n > 1 and not np.allclose(np.diff(rec.t_ms), dt_ms, atol=1e-6):
        issues.append(f"采样间隔偏离 {dt_ms} ms")
    if not np.all(np.isfinite(rec.p_raw)):
        issues.append("p_raw 含 NaN/inf")
    bad = set(np.unique(rec.phase).tolist()) - set(PHASES)
    if bad:
        issues.append(f"非法 phase 值：{sorted(bad)}")
    return issues
