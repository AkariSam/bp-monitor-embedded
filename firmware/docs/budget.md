# S5 内存/耗时预算实测（整改后）

日期：2026-10-24
测量脚本：`firmware/scripts/budget.py`
被测对象：`firmware/buffer.py` 的 `DeflateBuffer` + `firmware/analyzer.py` 的 `PipelineAnalyzer`

## 0. 方法与口径

- **数据**：`tmp/budget_sim/normal_s001.csv`（seed 1，正常场景波形；放气速率调慢到
  DEFLATE 恰好 9600 点，即 80 Hz × 120 s 的合同上限）。
- **内存**：CPython `sys.getsizeof` + 递归深占用；对共享对象按 `id` 去重。
  另按 32 位 MicroPython 对象模型估算设备占用：
  - `array('H')`：2 B/元素 + 32 B 对象头；`array('f')`：4 B/元素 + 32 B 对象头；
  - list/tuple：4 B/槽 + 16 B 对象头；float 对象 12 B（8 B 对象 + 4 B GC）；
  - 小整数立即数 0 B；普通对象 24 B + 属性；字符串 16 B + 长度。
- **耗时**：`time.perf_counter()`，每个规模重复 20 次取中位数；设备倍率按
  ×50（乐观）与 ×100（保守）估算。阶段预算沿用 tech-plan §4.1 的 250 ms。
  PC 计时为墙钟时间，重复运行的中位数约 ±10%（受机器负载影响）。
- **CI 口径**：共享 CI runner 的墙钟时间噪声更大，`act4` 在 `CI=true` 时只做
  500 ms 粗筛；250 ms 硬门槛在本地或目标硬件上执行，设置 `BUDGET_STRICT=1`
  可强制 CI 也按 250 ms 断言。
- 200 KiB 为保守堆预算（Pico 2 W MicroPython 实际可用堆通常更大，但按最坏情况规划）。

## 1. DeflateBuffer(9600) 实际占用

| 对象 | 类型 | 元素 | 原始数据 | sys.getsizeof |
|---|---|---:|---:|---:|
| `t_ms`（相邻增量） | `array('H')` | 9600 | 18.8 KiB | 20.0 KiB |
| `pressure_centimmHg` | `array('H')` | 9600 | 18.8 KiB | 20.0 KiB |
| `seq`（`first_seq + i` 推导） | — | 9600 | 0.0 KiB | 0.0 KiB |
| **合计** | 2 个 array | — | **37.5 KiB** | **40.0 KiB** |

原始数据 37.5 KiB = 38.4 KB（十进制）；对象头与字段另约 336 B。
CPython `array` 有约 6.5% 扩容余量，故 `sys.getsizeof` 比原始数据大；
MicroPython `array` 按精确长度估算。

## 2. PipelineAnalyzer 逐阶段内存（9600 点）

| 阶段 | 新增对象 | CPython 深占用 | MicroPython 估算 | 容器表面 | 占 200 KiB |
|---|---|---:|---:|---:|---:|
| prepare | `p_view`, `time_view` | 42.0 KiB | 38.2 KiB | 40.2 KiB | 19% |
| dc_init | +`dc` | 81.9 KiB | 75.7 KiB | 80.1 KiB | 38% |
| dc_first | 半段 DC | 81.9 KiB | 75.7 KiB | 80.1 KiB | 38% |
| dc_second | +`dc_view` | 82.3 KiB | 75.8 KiB | 80.1 KiB | 38% |
| ac_init | +`ac` | 122.2 KiB | 113.3 KiB | 120.1 KiB | 57% |
| ac_hp | HP 级 | 122.2 KiB | 113.3 KiB | 120.1 KiB | 57% |
| ac_lp | +`ac_view` | 122.5 KiB | 113.3 KiB | 120.1 KiB | 57% |
| peaks | +`candidates`, `accepted` | 144.1 KiB | 123.3 KiB | 121.4 KiB | 62% |
| envelope | +`envelope` | 151.9 KiB | 127.0 KiB | 121.5 KiB | 64% |
| decision | +`decision` | 152.9 KiB | 127.5 KiB | 121.5 KiB | 64% |
| qc | +`qc` | 153.4 KiB | 127.6 KiB | 121.6 KiB | 64% |

峰值阶段为 **qc**，MicroPython 估算 **127.6 KiB**（200 KiB 预算的 64%）。
其中 buffer 37.8 KiB、analyzer 89.8 KiB；相对 160 KiB 目标余量 32.4 KiB。

峰值阶段逐对象：

| 对象 | 类型 | 元素 | MP 增量 |
|---|---|---:|---:|
| `buffer` | `DeflateBuffer` | 9600 | 37.8 KiB |
| `p_view` / `time_view` | 视图 | 9600 | 0.3 KiB |
| `dc` | `array('f')` | 9600 | 37.5 KiB |
| `ac` | `array('f')` | 9600 | 37.5 KiB |
| `dc_view` / `ac_view` | `_SliceArray` | 9520 | 0.2 KiB |
| `candidates` / `accepted` | list | 76 | 10.0 KiB |
| `envelope` / `decision` / `qc` | object | — | 4.3 KiB |

### 2.1 CPython 全流程交叉检查

`tracemalloc` 跟踪整个分析过程（buffer 在起点前已分配，单独相加）：

- 分析过程 current 110.6 KiB、peak 120.5 KiB；
- 加 buffer 40.3 KiB 后全流程峰值 **160.9 KiB**（CPython）。

静态 qc 快照 153.4 KiB 与之一致；差额主要来自滤波/峰检测的短命临时对象。
MicroPython 估算的静态峰值 127.6 KiB 已计入块阈值与候选列表，临时对象
约再增加数 KiB，仍远低于 160 KiB 目标。

## 3. 分阶段耗时（4800 点，PC，20 次中位数）

| 阶段 | PC 中位耗时 | ×50 估算 | ×100 估算 | ≤250 ms（×100） |
|---|---:|---:|---:|---|
| prepare | 0.002 ms | 0 ms | 0 ms | 是 |
| dc_init | 0.369 ms | 18 ms | 37 ms | 是 |
| dc_first | 0.237 ms | 12 ms | 24 ms | 是 |
| dc_second | 0.446 ms | 22 ms | 45 ms | 是 |
| ac_init | 0.373 ms | 19 ms | 37 ms | 是 |
| ac_hp | 0.476 ms | 24 ms | 48 ms | 是 |
| ac_lp | 0.760 ms | 38 ms | 76 ms | 是 |
| peaks | 0.658 ms | 33 ms | 66 ms | 是 |
| envelope | 0.078 ms | 4 ms | 8 ms | 是 |
| decision | 0.017 ms | 1 ms | 2 ms | 是 |
| qc | 0.015 ms | 1 ms | 1 ms | 是 |
| **合计** | **3.431 ms** | **172 ms** | **343 ms** | — |

## 4. 分阶段耗时（9600 点最坏情况）

| 阶段 | PC 中位耗时 | ×50 估算 | ×100 估算 | ≤250 ms（×100） |
|---|---:|---:|---:|---|
| prepare | 0.002 ms | 0 ms | 0 ms | 是 |
| dc_init | 0.695 ms | 35 ms | 70 ms | 是 |
| dc_first | 0.484 ms | 24 ms | 48 ms | 是 |
| dc_second | 0.942 ms | 47 ms | 94 ms | 是 |
| ac_init | 0.757 ms | 38 ms | 76 ms | 是 |
| ac_hp | 1.066 ms | 53 ms | 107 ms | 是 |
| ac_lp | 1.480 ms | 74 ms | 148 ms | 是 |
| peaks | 1.416 ms | 71 ms | 142 ms | 是 |
| envelope | 0.265 ms | 13 ms | 26 ms | 是 |
| decision | 0.037 ms | 2 ms | 4 ms | 是 |
| qc | 0.025 ms | 1 ms | 2 ms | 是 |
| **合计** | **7.169 ms** | **358 ms** | **717 ms** | — |

9600 点最慢阶段为 AC LP，×100 估算 148 ms（预算的 59%）；全部阶段均满足
250 ms。实际仿真的 DEFLATE 段为 3734 点（3 mmHg/s），耗时约为表中 9600 点
的 39%，余量更大。

## 5. 整改后预算余量

| 项目 | 估算 |
|---|---:|
| buffer（2 个 `array('H')`） | 37.8 KiB |
| analyzer 峰值（含临时对象） | 89.8 KiB |
| 合计 | **127.6 KiB** |
| 200 KiB 余量 | 72.4 KiB（36%） |
| 160 KiB 目标 | **达标**（余量 32.4 KiB） |

## 6. 结论

1. **内存已从 647.4 KiB 降到 127.6 KiB**（MicroPython 估算），为 200 KiB
   预算的 64%，相对 160 KiB 目标仍余 32.4 KiB。整改项：
   - `seq` 改为 `first_seq + i` 推导，`t_ms` 存 16-bit 相邻增量，
     压力改 `array('H')` centi-mmHg（buffer 112.8 → 37.8 KiB）；
   - `dc/ac` 改 `array('f')`，滤波与延迟补偿全部原地完成，不保留裁剪副本；
   - `t_s` 不落数组，`_TimeView` 按峰索引从 `t_ms` 增量累计取时刻；
   - 峰检测梯度改块阈值（只存约 96 个值），不再分配 n 长度临时列表。
2. **耗时已全部进入 250 ms/阶段预算**：9600 点最慢 AC LP 148 ms（×100），
   峰检测 142 ms；4800 点最慢 AC LP 76 ms。DC 拆成前后两半、AC 拆成
   HP/LP 两步，状态跨阶段续接，控制层会话协议不变。
3. **array('f') 存储的精度代价**：8 场景同缓冲对拍中，
   SBP 最大差 `9.0e-5 mmHg`、DBP 最大差 `9.7e-5 mmHg`、HR 差 0；
   PC 原始口径 HR 差仍为 `0.264 bpm`（1 ms 时间取整）。真值 MAE 保持
   SBP 0.4248 / DBP 0.8997 mmHg，与整改前一致，远低于 1 mmHg 判据。
4. 未改动 `algo/`、`control_fsm.py`、`drivers/`；`config.py` 无需改动。

## 7. 风险与说明

- MicroPython 估算依赖对象模型假设（float 12 B、list 槽 4 B、GC 头 4 B）；
  当前已无 n 长度 float 列表，结论对 float 对象尺寸不敏感。
- PC 耗时不含 MicroPython 解释器与 GC 的额外抖动；×50–100 是经验倍率，
  需上机后用 `time.ticks_us()` 实测校核。9600 点 AC LP 为 59% 预算，
  仍有约 100 ms 余量。
- `seq` 推导仅用于分析缓冲；实测丢样/抖动仍由驱动日志的实测 `seq/t_ms`
  统计。`t_ms` 增量用 16-bit 时需保证单次间隔 <65.5 s。
- 内存测量基于 9600 点、76 个候选峰的正常形态数据；高心率场景候选数增加，
  但 `candidates/envelope` 仅约 10 KiB，不影响量级结论。
- 收敛后需在 RP2350 实机复核堆峰值与 `time.ticks_us()` 分阶段耗时。

## 8. 复现

```bash
cd BloodPressureMonitor
PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
  python firmware/scripts/budget.py
```

脚本首次运行会在 `tmp/budget_sim/` 生成 9600 点慢放气记录（seed 1，确定性）。

