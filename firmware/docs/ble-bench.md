# Phase 2A BLE Bench 说明

状态：WIP。Phase 2A 只验证 **Pico 2W 上的 GATT 传输 + 合成数据**，不接压力传感器、泵、阀和袖带，不做真实测量。

## 1. 目标

让手机 APP 在真机上完成：

```text
扫描 0x1810
→ 连接
→ 发现服务/特征
→ 订阅压力、状态、错误、0x2A35
→ 写入 0x01 START
→ 收到状态变化和 1 Hz 压力
→ 收到 0x2A35 结果 + completed
→ 写入 0x02 STOP 或测试 REJECT/FAULT
```

## 2. 硬件与固件

- Raspberry Pi Pico 2W；
- Android 手机（安装 APP）；
- USB 线；
- MicroPython 固件；
- `aioble` 包。

不需要：压力传感器、泵、阀、袖带、PCB。

建议先记录：

```text
MicroPython 版本：
aioble 版本/来源：
Pico 2W 板子：
手机型号/Android 版本：
APP 版本：
```

## 3. 安装 aioble

用 Thonny：

```text
Tools → Manage Packages → 搜索 aioble → Install
```

或者把 `aioble` 包复制到 Pico 文件系统，确保 `import aioble` 不报错。

## 4. 复制文件到 Pico

至少复制：

```text
config.py
ble_protocol.py
ble_tx.py
ble_bench_core.py
ble_server.py
ble_bench.py
aioble/
```

可选：把 `ble_bench.py` 作为 `main.py`，或新建：

```python
import ble_bench
ble_bench.run()
```

## 5. 先做 5 分钟冒烟测试

在写完整服务前，先用官方 `aioble` Notify 示例或一个最小 Battery Notify 服务确认：

- 手机能看到广播；
- 能连接；
- 能发现服务；
- 能收到 Notify。

背景：RP2350 上有一个未关闭的 `aioble notify()` 问题：

```text
https://github.com/micropython/micropython/issues/19019
```

维护者怀疑示例漏了 `aioble.register_services()`，但没有最终结论。如果最小 Notify 示例都收不到数据，先不要继续调试完整服务，直接评估 raw `bluetooth.BLE` 方案。

## 6. 运行

在 Pico 的 REPL：

```python
import ble_bench
ble_bench.run()
```

设备名：

```text
BP-Monitor-Bench
```

广播必须包含标准血压服务：

```text
0x1810
```

## 7. 合成场景

每次 START 按顺序循环：

| 次数 | 场景 | 期望 |
|---:|---|---|
| 1 | OK | 结果 `0x2A35`，然后 `completed(4)` |
| 2 | REJECT | 错误码 `0x03`，然后 `error(5)`，无结果 |
| 3 | FAULT | 错误码 `0x04`，然后 `error(5)`，无结果 |
| 4 | 回到 OK | 同第 1 次 |

测量中写 `0x02`：`dumping(8)` → `idle(0)`，不发结果。

OK 结果固定为：

```text
SBP 120 / DBP 80 / MAP 93 / HR 72
04 78 00 50 00 5D 00 48 00
```

## 8. 手机验收清单

| 编号 | 操作 | 预期 |
|---|---|---|
| BLE-01 | 扫描 | 能看到 `BP-Monitor-Bench`，广播含 `0x1810` |
| BLE-02 | 连接 | 连接成功，服务发现完成 |
| BLE-03 | 订阅 | 压力/状态/错误/`0x2A35` 四个订阅成功 |
| BLE-04 | 写 `0x01` | 进入 selfCheck → inflate → settle → deflate → analyze |
| BLE-05 | 观察压力 | 1 Hz 左右更新，数值随阶段变化 |
| BLE-06 | 第一轮完成 | 收到 9 字节结果，再收到 `completed(4)` |
| BLE-07 | 第二轮完成 | 收到 `0x03`，再收到 `error(5)`，无结果 |
| BLE-08 | 第三轮完成 | 收到 `0x04`，再收到 `error(5)`，无结果 |
| BLE-09 | 测量中写 `0x02` | 先 `dumping(8)`，再 `idle(0)`，无结果 |
| BLE-10 | 断线重连 | 重连后收到当前状态；不补发历史结果 |
| BLE-11 | 连续 5 分钟 | 不死机、不断连、无异常复位 |

记录表：

```text
BLE-01: PASS/FAIL  备注：
BLE-02: PASS/FAIL  备注：
...
```

## 9. 故障排查

### 手机扫不到设备

- 确认 `ble_bench.run()` 正在运行；
- 确认没有旧连接占用；
- 重启 Pico 和手机蓝牙；
- 检查广播是否包含 `0x1810`；
- 换 nRF Connect 交叉验证。

### 能连接但收不到 Notify

- 先跑最小 Notify 冒烟测试；
- 检查 CCCD 是否订阅；
- 检查 MicroPython/aioble 版本；
- 参考 issue #19019；
- 必要时切 raw `bluetooth.BLE` 方案。

### 能收到压力但收不到结果

- 确认已订阅 `0x2A35` 的 Indicate/CCCD；
- 确认第一轮是 OK 场景；
- 检查结果 9 字节编码；
- 检查 APP 是否按标准 SFLOAT 解析。

### 能收到结果但顺序不对

- 检查 `ble_tx.tx_events()`；
- 结果必须先于 `completed(4)`；
- 错误码必须先于 `error(5)`；
- 不要在发送之间插入额外状态。

## 10. 边界

- 不接泵阀，不做真实充气；
- 不验证测量准确性；
- 不使用设备端历史；
- 不做应用层 ACK/重传；
- 当前不是医疗器械，不能用于诊断或临床决策。
