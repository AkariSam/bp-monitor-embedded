# 驱动接口对接说明（真实硬件 → 控制层/算法层）

适用：`firmware/` 当前代码（控制层 `control_fsm.py`、算法 `analyzer.py`）。日期：2026-11-07。

## 0. 一句话任务

实现一套真实硬件驱动，**替换 `firmware/drivers/fake_driver.py`**，让
`ControlFSM` + `PipelineAnalyzer` 原样运行；控制层、算法层、状态机、数据格式都不要改。
`fake_driver.py` 是接口参考实现，不是要上板的生产代码。

本文件以**当前 firmware 代码**为准。采样数据契约仍未最终定版；BLE 已按
`docs/interfaces/ble-gatt-protocol.md` v1.0 设计冻结。**驱动 API 的四元组与 `_publish` 字段已经在代码里冻结**，
按本文实现即可。

## 1. 接口总览

| 方法 | 必需 | 作用 |
|---|---|---|
| `pressure_read()` | 是 | 返回最新压力样本 `(seq, t_ms, p_mmHg, status)` |
| `now_ms()` | 是 | 单调毫秒时钟，供控制层做超时/状态计时 |
| `pump_set(on)` | 是 | 泵全速开/关 |
| `valve_set_energized(on)` | 是 | 阀线圈通电/失电（失电=泄压） |
| `safe_state()` | 是 | 复合安全动作：泵停 + 阀失电 |
| `display_render(frame)` | 是（可 no-op） | 渲染状态帧到 OLED |
| `ble_send(frame)` | 是（可 no-op） | 发送状态帧到 BLE/APP |
| `ble_tick()` | 可选（真实 BLE 必需） | 由主循环调用，按 1 Hz 发送当前压力；内部节流 |
| `poll_events()` | 可选 | 本地按键/事件队列 |
| `ble_poll()` | 可选 | BLE 下行事件队列 |

`ControlFSM.__init__` 会立刻调用一次 `driver.now_ms()`，所以驱动对象构造完成后时钟必须可用。

## 2. 方法契约

### 2.1 `pressure_read()`

```python
def pressure_read(self):
    # 返回 (seq, t_ms, p_mmHg, status)
    return (seq, t_ms, p_mmHg, status)
```

| 字段 | 类型 | 语义 |
|---|---|---|
| `seq` | int | 样本序号，单调不减；每个新样本（包括 `NOT_READY` 的新采样）递增；重复返回同一缓存样本时保持不变；不得回退 |
| `t_ms` | int | 该样本的采集时刻，绝对毫秒；单调不减；重复缓存样本时保持不变 |
| `p_mmHg` | float/int | 校准后的袖带压 [mmHg]；保留两位小数即可 |
| `status` | int | 状态位，见下表；必须始终返回数值，不能是 `None` |

`status` 位定义（与 `config.py` 完全一致，禁止改位）：

| 位 | 值 | 含义 | 控制层行为 |
|---|---:|---|---|
| `STATUS_OK` | 0 | 有效新样本 | 正常使用 |
| `STATUS_NOT_READY` | 0x01 | 还没有有效样本/正在预热/转换未完成 | 测量态连续 >500 ms 判 `SENSOR_NOT_READY_TIMEOUT`；SELF_CHECK 最多等 2 s |
| `STATUS_COMM_FAIL` | 0x02 | 传感器通信失败 | 立即故障、安全动作 |
| `STATUS_STALE` | 0x04 | 样本过期（驱动定义阈值并记录） | 立即故障、安全动作 |
| `STATUS_SATURATED` | 0x08 | 传感器饱和 | 立即故障、安全动作 |
| `STATUS_OUT_OF_RANGE` | 0x10 | 超量程/非法读数 | 立即故障、安全动作 |

- `STATUS_SAFETY_FAULTS = COMM_FAIL | STALE | SATURATED | OUT_OF_RANGE`；
  `STATUS_KNOWN = NOT_READY | STATUS_SAFETY_FAULTS`。返回未知位时：只含未知位按
  `OUT_OF_RANGE` 故障；若同时带安全位，安全位优先。
- `NOT_READY` 是正常等待，不要用 `COMM_FAIL` 代替；也不要因为“这次没读到新样本”就报通信失败。
- `p_mmHg < -5.0`、`NaN`、未知状态位都会触发故障。**不要把 260/280 mmHg 超压钳低**，
  控制层必须看到真实压力才能触发超压保护；只在传感器物理量程处饱和并置位 `SATURATED`。
- **不要返回 `None` 或短元组**。即使 `NOT_READY`，也要返回 4 元组；`p_mmHg` 可用最后有效值或 0.0。
- `pressure_read()` 可以抛异常（I2C/UART 真异常），控制层会转成 `SENSOR_COMM_FAIL` 故障；
  但可预期的“未就绪/饱和/超量程”应通过 `status` 表达，不要靠异常。
- 采样目标 **80 Hz**（12.5 ms 一个新样本）。控制层可能比 80 Hz 更频繁地调用
  `pressure_read()`：重复调用返回**同一个缓存样本**（`seq/t_ms/p` 都不变）是正确行为，
  控制层按 `seq` 去重，不会重复入缓冲。
- **DEFLATE 段内 `seq` 回退会被判 `SENSOR_SEQ_REGRESSION` 故障**；`seq` 跳号允许（例如丢样），
  但分析缓冲的 `seq` 是按 `first_seq + i` 推导的，丢样统计请以驱动日志/CSV 的实测 `seq` 为准。
- **DEFLATE 段内** `t_ms` 相邻增量必须小于 65535 ms（正常 12.5 ms）；超过会让
  `DeflateBuffer.append()` 抛异常，控制层报 `BUFFER_OVERFLOW`。

### 2.2 `now_ms()`

```python
def now_ms(self):
    return monotonic_ms  # int
```

- 单调不减的毫秒整数；控制层所有超时/状态计时都以它为准。
- **必须自己处理 `time.ticks_ms()` 回绕**（例如维护回绕计数，或用 `ticks_diff`），
  不能把会回绕的原始值直接返回；否则 `now - start` 会出现负数并误触发/漏触发超时。
- 不要用 `sleep()` 推进时间；驱动内部不要阻塞等待采样。

### 2.3 `pump_set(on)` / `valve_set_energized(on)`

```python
def pump_set(self, on):              # on: bool
def valve_set_energized(self, on):   # on: bool
```

- `pump_set(True)` = 泵全速通电；`False` = 停泵。幂等，内部不延时、不做目标压力/PWM/PID。
- `valve_set_energized(True)` = 阀线圈通电，三通阀 **1–2 通**（保压/测量）；
  `False` = 失电，**1–3 通大气**（泄压）。默认必须是失电。
- 对外只暴露“通电/失电”语义；GPIO 高/低有效、ULN2003 反相、外部下拉由驱动内部处理，
  控制层不关心电平极性。
- 阀的硬件默认态必须满足**断电即泄压**（三通阀，或常开泄压阀＋单向阀）。
  二通常闭阀不能作为默认方案；若用替代阀，必须证明失电时气路通大气。
- 异常约定：`pump_set`/`valve_set_energized` 抛异常 → 控制层记 `ACTUATOR_FAILED` 并执行
  `safe_state()`；驱动应尽量先把硬件置安全再抛，至少不能把泵/阀留在危险状态。
- 控制层会缓存最后一次成功写入的 `pump_on/valve_energized`；不要在 API 之外偷偷改泵阀状态。

### 2.4 `safe_state()`

```python
def safe_state(self):
    # 立即：泵停 + 阀失电；任何时刻可调用
```

- 复合安全动作，必须**直接写 GPIO**，不依赖 OLED、BLE、网络、动态内存或 `now_ms()`。
- 幂等、非阻塞；重复调用安全。
- 实现建议：先尝试关泵、再失电阀；两个动作都做，不要因为第一个失败就跳过第二个。
  如果任一通道无法确认安全，可以抛异常。
- 控制层兜底语义：`safe_state()` 抛异常后，FSM 会按自己缓存的通道状态分别调用
  `pump_set(False)` / `valve_set_energized(False)`；两路都成功则记
  `SAFE_STATE_FALLBACK_OK` 并继续；仍失败则 `_safe_state_pending=True`，
  停在 DUMP 每步重试，**成功前不进入 DONE**。
- `fsm.is_healthy()` 只在没有待重试安全动作时为真；`safe_state` 失败期间为假，
  主循环必须停止喂 WDT（见 §4）。
- 上电/复位初始化时也要调用或达到同样的安全状态；不能等第一次 START 才关泵阀。

### 2.5 `display_render(frame)` / `ble_send(frame)`

```python
def display_render(self, frame):   # frame: dict
def ble_send(self, frame):         # frame: dict
```

`_publish()` 在**每次状态迁移**时生成同一个 dict，传给这两个方法（不是每采样一帧）：

| 键 | 类型 | 含义 |
|---|---|---|
| `type` | str | 固定 `"STATE"` |
| `t_ms` | int | 发布时刻（`driver.now_ms()`） |
| `state` | str | 8 状态之一：`IDLE/SELF_CHECK/INFLATE/SETTLE/DEFLATE/DUMP/ANALYZE/DONE` |
| `result` | str/None | DONE 时 `OK/REJECT/CANCEL/FAULT`，否则 `None` |
| `reason` | str/None | 原因码；`result=None` 时为 `None` |
| `p_mmHg` | float/None | 最近一次有效压力；尚未采到有效值时为 `None` |
| `pump` | bool | 控制层缓存的泵命令 |
| `valve` | bool | 控制层缓存的阀命令（True=通电保压） |
| `data` | dict/None | 仅 `DONE(OK)` 时非空；包含 `sbp/dbp/map/hr` 及分析器诊断字段；其他状态为 `None` |

- 帧只在状态迁移时发布（`_enter`）；APP 的实时压力曲线由 `ble_tick()` 以 1 Hz 发送，
  不能假设 `ble_send` 每 12.5 ms 一帧。
- `display_render`/`ble_send` 必须**非阻塞**，并说明最大阻塞时间；OLED 可丢帧/限帧率，
  BLE 可丢帧/超时。回调里不要反向调用 `ControlFSM`。
- 异常会被捕获并计入 `display_errors`/`ble_errors`，不影响测量与安全动作；
  但不要依赖“异常被吞”来实现流程。
- 两者缺失时每个状态迁移都会计一次错误；没有显示/蓝牙也应实现空方法。
- `frame["data"]` 是 GATT `0x2A35` 的唯一数据来源。`DONE(OK)` 时必须包含
  `sbp/dbp/map/hr`；`REJECT/FAULT/CANCEL` 时为 `None`，驱动不得发送结果帧。
- `ble_send(frame)` 只处理状态迁移；实时压力曲线由 `ble_tick()` 以 1 Hz 节流发送。
  `ble_tick()` 应读取驱动缓存的最新压力，不阻塞、不改变控制层状态；发送失败只计数，
  不影响测量与安全动作。主循环每 5–10 ms 调用一次即可，由驱动内部按 1000 ms 节流。

### 2.6 `poll_events()` / `ble_poll()`

```python
def poll_events(self):   # 可选：按键/本地事件；返回 list/tuple/None
def ble_poll(self):      # 可选：BLE 下行事件；返回 list/tuple/None
```

- 事件字符串只有：`START`、`STOP`、`CONFIRM`。大小写不敏感，首尾空白会被去掉。
- 返回 `list`/`tuple` 最清晰；`None` 表示无事件；生成器/bytes 也可接受。
  **返回 `dict` 会被忽略**（避免把配置键当事件）；非字符串元素会被跳过。
- `START`：IDLE/DONE 下开始测量；其他状态忽略。
- `STOP`：测量态先执行安全动作，再进入 DUMP/CANCEL；DONE 下回 IDLE。
- `CONFIRM`：DONE 下回 IDLE；其他状态忽略。
- 两个方法都可选（FSM 用 `hasattr` 判断）；但没有 BLE 时至少要能通过 `poll_events`
  或外部 `fsm.start()/stop()/confirm()` 注入事件。
- 异常会被捕获并计数（`event_poll_errors`/`ble_errors`），不阻塞控制循环。

### 2.7 测试专用方法（不属于生产契约）

`FakeDriver` 还有 `advance()`、`set_pressure()`、`set_sensor_status()`、`fail_*`、
`queue_event()`、`reset_seq()` 等，这些是台架测试钩子，**真实驱动不需要实现**。
需要跑 demo 的故障注入场景时，用单独的 Bench 适配器包一层，不要把测试钩子塞进生产驱动。

## 3. 上电默认安全态

驱动初始化顺序必须是：

1. 先把泵控制脚置为“停泵”电平，把阀控制脚置为“失电”电平；
2. 确认外部下拉/驱动级默认态（ULN2003/MOSFET 栅极下拉）有效；
3. 再初始化传感器、OLED、BLE、WDT；
4. 上电后任何初始化失败都保持泵停、阀失电，不得进入充气。

验收时用万用表/示波器确认：上电、复位、WDT 复位三种情况下泵不转、阀失电、袖带可泄压。
`ControlFSM` 构造时只读时钟，不会自动关泵阀，所以这个安全态必须由驱动初始化保证。

## 4. 主循环集成

```python
import time
from machine import WDT
from control_fsm import ControlFSM
from analyzer import PipelineAnalyzer
from my_driver import RealDriver

driver = RealDriver()                 # 初始化即泵停/阀失电
wdt = WDT(timeout=500)                # 驱动岗初始化，500 ms
fsm = ControlFSM(driver, analyzer=PipelineAnalyzer())
fsm.start()                           # 或由按键/BLE START 触发

while True:
    fsm.step()
    driver.ble_tick()                 # 1 Hz 压力通知，内部节流、非阻塞
    if fsm.is_healthy():
        wdt.feed()                    # 只在健康时喂；pending 时停止喂
    time.sleep_ms(5)                  # 5–10 ms；必须快于 80 Hz 采样
    if fsm.state == "DONE":
        # 显示结果；等待 CONFIRM/STOP；不要停止调用 step()
        pass
```

- **80 Hz 采样 vs `step()`**：采样 12.5 ms 一个新样本；`step()` 建议 5–10 ms（最慢不超过
  12 ms），比采样更频繁，靠 `seq` 去重。若 `step()` 慢于 12.5 ms，驱动只保留最新样本，
  中间样本会丢；当前接口没有多样本 FIFO。
- **WDT 硬性要求**：只在 `fsm.is_healthy()` 为真时喂狗。`is_healthy()` 为假表示
  `safe_state()` 尚未成功/仍在重试；此时继续喂狗会掩盖安全故障。
  WDT 复位后 GPIO 高阻 + 外部下拉 → 泵停、阀失电，这是最后一道保护。
- `is_healthy()` 只反映“安全动作是否完成”，不是完整系统健康检查；主循环还应保证
  `step()` 正常返回、采样未抛异常。不要把它误当成“所有传感器都正常”。
- ANALYZE 分 11 个有界阶段，单阶段 PC ×100 估算 <250 ms，WDT 500 ms；阶段间只在
  `is_healthy()` 时喂狗。驱动调用（尤其 OLED/BLE）不能阻塞超过 WDT 周期。
- 不要用 `sleep(1)` 等长阻塞；驱动内部更不能用忙等。

## 5. 控制层关键行为（仅供理解，不要在驱动里实现）

`NOT_READY` 在测量态连续 >500 ms 超时、SELF_CHECK 总预算 2 s；260 mmHg 立即安全动作、
280 mmHg 锁存；充气目标 180、静置 1 s、40 mmHg 快排、<15 mmHg 才分析、总超时 120 s。
这些逻辑都在控制层，驱动只提供真实压力、状态位和泵阀动作。

## 6. 验收路径

### 6.1 PC/台架（先做）

1. 用串口/调试板读真实传感器，实现 `pressure_read/now_ms/pump_set/valve_set_energized/
   safe_state/display_render/ble_send`；OLED/BLE 可先 no-op。
2. 跑现有回归，确认没破坏控制层：
   `python -m pytest firmware/tests algo/tests -q`（预期 235 passed）。
3. **normal 全流程**：`run_demo.py` 场景 1 接受 `driver_factory`；用真实驱动（或
   “半真实”驱动：真实传感器 + 合成脉搏）跑一次 START→DONE。台架上如果没有人体脉搏，
   需要脉搏源/袖带模拟器；静态压力不会产生包络，算法会判废。
4. **故障注入 5 场景**：当前 `run_demo.py` 的场景 2–5 依赖 `FakeDriver` 的测试钩子
   （`set_pressure/set_sensor_status/fail_*/queue_*`）。真实驱动接入时，推荐写一个
   **BenchAdapter**（组合真实驱动 + 测试钩子）来跑这 5 个场景；不要把测试钩子加进生产驱动。
   或者在台架上手动复现：STOP、260/280 超压、传感器断开、WDT 复位。
5. 跑 `python firmware/demo/run_algo_demo.py` 确认算法/对拍/预算仍全绿（4/4）。

### 6.2 上板（再做）

按同一顺序手工复现：正常充放气/分析 → STOP（泵停、阀失电、DUMP、不 ANALYZE）→
260/280 超压（安全动作+故障码）→ 拔传感器/断 I2C（COMM_FAIL）→ WDT 复位（泵停/阀失电/
可泄压）→ 120 s 边界（9600 点不溢出、`t_ms` 正常）。

## 7. 验收 checklist

- [ ] 上电/复位/WDT 复位：泵不转、阀失电、袖带可泄压（万用表/示波器确认）
- [ ] `now_ms()` 单调不减，跨 `ticks_ms()` 回绕不倒退
- [ ] `pressure_read()` 80 Hz；`seq` 单调；重复读返回同一缓存样本；`t_ms` 间隔 <65.5 s
- [ ] 状态位与 `config.py` 一致：OK/NOT_READY/COMM_FAIL/STALE/SATURATED/OUT_OF_RANGE
- [ ] `NOT_READY` 不是通信失败；测量态 >500 ms 才超时，SELF_CHECK 最多 2 s；`p<-5`/NaN/未知位 → OUT_OF_RANGE
- [ ] 260 mmHg → 安全动作 + `OVERPRESSURE_260`；280 mmHg → `OVERPRESSURE_LATCHED_280`；压力不被钳低
- [ ] `pump_set`/`valve_set_energized` 幂等；默认关/失电；无 PWM/PID；阀失电=1–3 通大气
- [ ] `safe_state()` 不依赖显示/BLE；重复调用安全；失败时 FSM 单路回退可用
- [ ] `safe_state` 失败期间 `fsm.is_healthy()==False`，WDT 停止喂狗；STOP 先安全动作、不 ANALYZE
- [ ] `display_render`/`ble_send` 非阻塞、帧字段一致；`ble_poll`/`poll_events` 返回 list/tuple，事件生效
- [ ] 主循环 `step()` 5–10 ms；80 Hz 不丢样；每个驱动调用 < WDT 500 ms
- [ ] 泵阀动作在 GPIO 层面实测；真实驱动 normal 全流程 ΔSBP/ΔDBP ≤1 mmHg（台架脉搏源）

## 8. 边界与联系

- **契约归属**：四元组 `(seq,t_ms,p_mmHg,status)`、状态位、8 状态、帧字段、事件名由
  控制/集成负责人冻结；驱动侧不要改名、加字段、改位定义或改事件拼写。
- **驱动不做业务**：不得内嵌测量状态迁移、目标压力、充气/放气策略、超时、重测、判废、
  算法/QC；这些都在 `control_fsm.py`/`analyzer.py`。
- **泵阀唯一入口**：除 `safe_state()` 外，泵阀只由控制层通过 `pump_set`/
  `valve_set_energized` 写入；驱动内部不要自行开泵/关阀做“保护”。
- **物理层变更**：tech-plan v2.9 的采集链/传感器型号尚未最终定版；如果换传感器、换接口、
  换阀，只改驱动内部实现，保持本文 API 与安全语义不变。若硬件确实无法满足
  （例如传感器只有 10 Hz、阀是二通常闭），先找组长沟通，不要私自改控制层语义。
- **BLE 字节格式**：以 `docs/interfaces/ble-gatt-protocol.md` v1.0 为唯一正本。
  `ble_send` 收到状态 dict；`frame["data"]` 仅在 `DONE(OK)` 时非空，驱动按 GATT
  §5.3 序列化 `0x2A35`；`ble_tick` 按 §5.2 发送 1 Hz 压力；驱动不解释测量业务。
- 参考实现：`firmware/drivers/fake_driver.py`；控制层调用点：`firmware/control_fsm.py`
  （`pressure_read`/`pump_set`/`valve_set_energized`/`safe_state`/`_publish`/`is_healthy`）。
