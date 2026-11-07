"""S5 内存/耗时预算实测（PC 侧）。

用法（包根目录）::

    python firmware/scripts/budget.py

测量对象：
- ``DeflateBuffer(9600)`` 的实际数组占用；
- ``PipelineAnalyzer`` 在 9600 点缓冲上逐阶段的中间对象占用（CPython 深占用
  + 按 MicroPython 对象模型折算的估算值）；
- 4800 点缓冲上 8 个分析阶段的 PC 耗时（20 次中位数）。

MicroPython 估算假设（32 位 RP2350，单精度 float）：
- ``array('i')`` / ``array('f')``：4 B/元素 + 32 B 对象头；
- list/tuple：4 B/槽 + 16 B 对象头；float 对象 12 B（8 B 对象 + 4 B GC）；
- 小整数立即数 0 B；字符串 16 B + 长度；普通对象 24 B + 属性；
- 切片与原 list 共享 float 对象（按 id 去重，与 MicroPython 语义一致）。
"""

import statistics
import sys
import time
import tracemalloc
from array import array
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FW_DIR = ROOT / "firmware"
ALGO_DIR = ROOT / "algo"
for path in (str(FW_DIR), str(ALGO_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from analyzer import PipelineAnalyzer  # noqa: E402
from bp.csvio import read_csv  # noqa: E402
from buffer import DeflateBuffer  # noqa: E402
from sim import scenarios  # noqa: E402
from sim.generate import generate  # noqa: E402

STAGES = (
    "prepare",
    "dc_init",
    "dc_first",
    "dc_second",
    "ac_init",
    "ac_hp",
    "ac_lp",
    "peaks",
    "envelope",
    "decision",
    "qc",
)
ATTRS = (
    "p_view",
    "time_view",
    "dc",
    "dc_view",
    "ac",
    "ac_view",
    "candidates",
    "accepted",
    "envelope",
    "decision",
    "qc",
)
REPS = 20
FULL_SAMPLES = 9600
TIMING_SAMPLES = 4800
HEAP_BUDGET_KIB = 200.0


def _generate_slow(outdir):
    physio, artifacts = scenarios.get_scenario("normal")
    rate = (physio.p_target - physio.p_deflate_end) * 80.0 / FULL_SAMPLES
    generate(
        "normal",
        1,
        outdir,
        physio=replace(physio, deflate_rate=rate),
        art=artifacts,
    )


def ensure_slow_recording():
    """生成 9600 点 DEFLATE 的真实形态记录（缓存在 tmp/budget_sim）。"""
    outdir = ROOT / "tmp" / "budget_sim"
    csv_path = outdir / "normal_s001.csv"
    if not csv_path.exists():
        _generate_slow(outdir)
    rec = read_csv(str(csv_path))
    if int((rec.phase == "DEFLATE").sum()) < FULL_SAMPLES:
        _generate_slow(outdir)
        rec = read_csv(str(csv_path))
    return rec


def build_buffer(rec, limit):
    mask = rec.phase == "DEFLATE"
    t_values = rec.t_ms[mask]
    p_values = rec.p_raw[mask]
    count = min(len(t_values), limit)
    buffer = DeflateBuffer(max(1, count))
    for i in range(count):
        if not buffer.append(i, int(round(float(t_values[i]))), float(p_values[i])):
            raise AssertionError("buffer append failed")
    return buffer


def cpy_size(obj, seen):
    oid = id(obj)
    if oid in seen:
        return 0
    seen.add(oid)
    size = sys.getsizeof(obj)
    if isinstance(obj, (list, tuple)):
        for item in obj:
            size += cpy_size(item, seen)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            size += cpy_size(key, seen) + cpy_size(value, seen)
    elif isinstance(obj, array):
        return size
    elif hasattr(obj, "__dict__"):
        size += cpy_size(vars(obj), seen)
    return size


def mp_size(obj, seen):
    oid = id(obj)
    if oid in seen:
        return 0
    seen.add(oid)
    if isinstance(obj, array):
        return obj.itemsize * len(obj) + 32
    if isinstance(obj, (list, tuple)):
        return 4 * len(obj) + 16 + sum(mp_size(item, seen) for item in obj)
    if isinstance(obj, dict):
        return (
            16
            + 8 * len(obj)
            + sum(mp_size(k, seen) + mp_size(v, seen) for k, v in obj.items())
        )
    if isinstance(obj, bool):
        return 0
    if isinstance(obj, float):
        return 12
    if isinstance(obj, int):
        return 0 if -(1 << 30) <= obj < (1 << 30) else 8
    if isinstance(obj, str):
        return 16 + len(obj)
    if obj is None:
        return 0
    if hasattr(obj, "__dict__"):
        return 24 + mp_size(vars(obj), seen)
    return 8


def snapshot(analyzer):
    """返回 (逐对象行, CPython 深总占用, MP 估算总占用, 容器表面总占用)。"""
    rows = []
    seen_cpy = set()
    seen_mp = set()
    total_cpy = cpy_size(analyzer.buffer, seen_cpy)
    total_mp = mp_size(analyzer.buffer, seen_mp)
    total_container = sys.getsizeof(analyzer.buffer)
    for name in ("t_ms", "pressure_centimmHg"):
        total_container += sys.getsizeof(getattr(analyzer.buffer, name))
    rows.append(
        (
            "buffer",
            "DeflateBuffer",
            len(analyzer.buffer),
            total_container,
            total_cpy,
            total_mp,
        )
    )
    for name in ATTRS:
        obj = getattr(analyzer, name, None)
        if obj is None:
            continue
        length = len(obj) if hasattr(obj, "__len__") else "-"
        own_cpy = cpy_size(obj, seen_cpy)
        own_mp = mp_size(obj, seen_mp)
        total_cpy += own_cpy
        total_mp += own_mp
        total_container += sys.getsizeof(obj)
        rows.append(
            (
                name,
                type(obj).__name__,
                length,
                sys.getsizeof(obj),
                own_cpy,
                own_mp,
            )
        )
    return rows, total_cpy, total_mp, total_container


def measure_memory(buffer):
    analyzer = PipelineAnalyzer()
    analyzer.start(buffer)
    stages = []
    for stage in STAGES:
        result = analyzer.step()
        rows, total_cpy, total_mp, total_container = snapshot(analyzer)
        stages.append(
            {
                "stage": stage,
                "rows": rows,
                "cpy": total_cpy,
                "mp": total_mp,
                "container": total_container,
                "result": result,
            }
        )
        if result is not None:
            break
    return stages


def measure_tracemalloc(buffer):
    """全流程 Python 分配峰值；buffer 在 start 前已分配，需单独相加。"""
    tracemalloc.start()
    analyzer = PipelineAnalyzer()
    analyzer.start(buffer)
    result = None
    while result is None:
        result = analyzer.step()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    buffer_bytes = sys.getsizeof(buffer) + sys.getsizeof(buffer.__dict__)
    for name in ("t_ms", "pressure_centimmHg"):
        buffer_bytes += sys.getsizeof(getattr(buffer, name))
    return current, peak, buffer_bytes, result


def measure_timing(buffer):
    timings = [[] for _ in STAGES]
    for _ in range(REPS):
        analyzer = PipelineAnalyzer()
        analyzer.start(buffer)
        for index in range(len(STAGES)):
            started = time.perf_counter()
            analyzer.step()
            timings[index].append((time.perf_counter() - started) * 1000.0)
    return [statistics.median(values) for values in timings]


def kib(value):
    return value / 1024.0


def print_buffer_table(buffer):
    print("## 1. DeflateBuffer(9600) 实际占用\n")
    print("| 对象 | 类型 | 元素 | 原始数据 | sys.getsizeof |")
    print("|---|---|---:|---:|---:|")
    raw_total = 0
    sys_total = 0
    for name in ("t_ms", "pressure_centimmHg"):
        values = getattr(buffer, name)
        raw = len(values) * values.itemsize
        raw_total += raw
        size = sys.getsizeof(values)
        sys_total += size
        print(
            "| %s | array('%s') | %d | %.1f KiB | %.1f KiB |"
            % (
                name,
                values.typecode,
                len(values),
                kib(raw),
                kib(size),
            )
        )
    print("| seq（推导） | first_seq + i | %d | 0.0 KiB | 0.0 KiB |" % len(buffer))
    print(
        "| 合计 | 2 个 array | — | %.1f KiB | %.1f KiB |"
        % (kib(raw_total), kib(sys_total))
    )
    print(
        "\n对象头/字段：`buffer` 本体 + `__dict__` ≈ %.0f B；"
        "原始数据 %.1f KiB（%.1f KB 十进制）。"
        % (
            sys.getsizeof(buffer) + sys.getsizeof(buffer.__dict__),
            kib(raw_total),
            raw_total / 1000.0,
        )
    )
    print()


def print_stage_memory(stages):
    print("## 2. PipelineAnalyzer 逐阶段内存（9600 点）\n")
    print(
        "| 阶段 | 新增对象 | CPython 深占用 | MicroPython 估算 | 容器表面 | 占 200 KiB |"
    )
    print("|---|---|---:|---:|---:|---:|")
    peak = max(stages, key=lambda item: item["mp"])
    for item in stages:
        names = []
        for row in item["rows"]:
            if row[0] not in ("buffer",) and row[0] not in names:
                names.append(row[0])
        print(
            "| %s | %s | %.1f KiB | %.1f KiB | %.1f KiB | %.0f%% |"
            % (
                item["stage"],
                ", ".join(names) if names else "—",
                kib(item["cpy"]),
                kib(item["mp"]),
                kib(item["container"]),
                100.0 * kib(item["mp"]) / HEAP_BUDGET_KIB,
            )
        )
    print(
        "\n峰值阶段：**%s**，MicroPython 估算 **%.1f KiB**（%.0f%% of %.0f KiB）。\n"
        % (
            peak["stage"],
            kib(peak["mp"]),
            100.0 * kib(peak["mp"]) / HEAP_BUDGET_KIB,
            HEAP_BUDGET_KIB,
        )
    )
    print("峰值阶段逐对象（按分配顺序，切片共享的 float 只计一次）：\n")
    print("| 对象 | 类型 | 元素 | sys.getsizeof | CPython 增量 | MP 增量 |")
    print("|---|---|---:|---:|---:|---:|")
    for row in peak["rows"]:
        print(
            "| %s | %s | %s | %.1f KiB | %.1f KiB | %.1f KiB |"
            % (
                row[0],
                row[1],
                row[2],
                kib(row[3]),
                kib(row[4]),
                kib(row[5]),
            )
        )
    print()
    return peak


def print_timing(timings, samples, index):
    total = sum(timings)
    print(
        "## %d. 分阶段耗时（%d 点，PC，%d 次中位数）\n"
        % (index, samples, REPS)
    )
    print("| 阶段 | PC 中位耗时 | ×50 估算 | ×100 估算 | ≤250 ms（×100） |")
    print("|---|---:|---:|---:|---|")
    for stage, value in zip(STAGES, timings):
        print(
            "| %s | %.3f ms | %.0f ms | %.0f ms | %s |"
            % (
                stage,
                value,
                value * 50.0,
                value * 100.0,
                "是" if value * 100.0 <= 250.0 else "**否**",
            )
        )
    print(
        "| 合计 | %.3f ms | %.0f ms | %.0f ms | — |"
        % (total, total * 50.0, total * 100.0)
    )
    print()


def print_budget(peak):
    current = kib(peak["mp"])
    buffer_kib = kib(peak["rows"][0][5])
    analyzer_kib = current - buffer_kib
    remaining = HEAP_BUDGET_KIB - current
    target = 160.0
    print("## 5. 整改后预算余量\n")
    print("| 项目 | 估算 |")
    print("|---|---:|")
    print("| buffer（2 个 array('H')） | %.1f KiB |" % buffer_kib)
    print("| analyzer 峰值（含临时对象） | %.1f KiB |" % analyzer_kib)
    print("| 合计 | **%.1f KiB** |" % current)
    print(
        "| 200 KiB 余量 | %.1f KiB（%.0f%%） |"
        % (remaining, 100.0 * remaining / HEAP_BUDGET_KIB)
    )
    print(
        "| 160 KiB 目标 | %s（余量 %.1f KiB） |"
        % (
            "达标" if current <= target else "未达标",
            target - current,
        )
    )
    print()


def main():
    rec = ensure_slow_recording()
    full = build_buffer(rec, FULL_SAMPLES)
    short = build_buffer(rec, TIMING_SAMPLES)
    print("# S5 内存/耗时预算实测\n")
    print(
        "数据：`tmp/budget_sim/normal_s001.csv`（seed 1，放气速率调慢到 "
        "9600 点 DEFLATE，波形形态与正常场景一致）。\n"
    )
    print_buffer_table(full)
    stages = measure_memory(full)
    peak = print_stage_memory(stages)
    tracemalloc_current, tracemalloc_peak, buffer_bytes, _result = (
        measure_tracemalloc(full)
    )
    print("### 2.1 tracemalloc 全流程峰值（CPython 交叉检查）\n")
    print(
        "- 分析过程 current %.1f KiB、peak %.1f KiB；"
        "加 buffer %.1f KiB 后峰值 %.1f KiB。"
        % (
            kib(tracemalloc_current),
            kib(tracemalloc_peak),
            kib(buffer_bytes),
            kib(tracemalloc_peak + buffer_bytes),
        )
    )
    print(
        "- 静态快照为阶段结束后的保留对象；tracemalloc 峰值用于交叉检查"
        "滤波/峰检测的临时分配。\n"
    )
    short_timings = measure_timing(short)
    print_timing(short_timings, TIMING_SAMPLES, 3)
    full_timings = measure_timing(full)
    print_timing(full_timings, FULL_SAMPLES, 4)
    print_budget(peak)

    over = kib(peak["mp"]) > HEAP_BUDGET_KIB
    print("## 6. 结论\n")
    print(
        "- 峰值 MicroPython 估算 **%.1f KiB**，%s %.0f KiB 预算"
        "（为预算的 %.0f%%）。"
        % (
            kib(peak["mp"]),
            "超过" if over else "低于",
            HEAP_BUDGET_KIB,
            100.0 * kib(peak["mp"]) / HEAP_BUDGET_KIB,
        )
    )
    print(
        "- 即便只计容器表面大小（不含 float 对象），峰值也有 **%.1f KiB**，"
        "因此超预算结论不依赖对象尺寸假设。"
        % kib(peak["container"])
    )
    worst_short = max(zip(STAGES, short_timings), key=lambda item: item[1])
    worst_full = max(zip(STAGES, full_timings), key=lambda item: item[1])
    print(
        "- 4800 点最慢阶段 **%s**：PC %.3f ms，×100 估算 %.0f ms（≤250 ms）。"
        % (worst_short[0], worst_short[1], worst_short[1] * 100.0)
    )
    print(
        "- 9600 点最慢阶段 **%s**：PC %.3f ms，×100 估算 %.0f ms（%s 250 ms）。"
        % (
            worst_full[0],
            worst_full[1],
            worst_full[1] * 100.0,
            "≤" if worst_full[1] * 100.0 <= 250.0 else ">",
        )
    )
    print(
        "- 4800 点总量：PC %.3f ms，×100 估算 %.0f ms；"
        "9600 点总量：PC %.3f ms，×100 估算 %.0f ms。"
        % (
            sum(short_timings),
            sum(short_timings) * 100.0,
            sum(full_timings),
            sum(full_timings) * 100.0,
        )
    )



if __name__ == "__main__":
    main()
