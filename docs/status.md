# 状态与已知边界

> 当前状态：WIP（2026-11-07 公开快照）
>
> 这是课程项目原型，不是医疗器械。当前代码不能用于诊断、治疗或临床决策。

## 已验证

- `python -m pytest firmware/tests algo/tests -q`：248 项通过；
- `firmware/demo/run_demo.py`：5/5 场景通过；
- `firmware/demo/run_algo_demo.py`：4/4 项通过；
- GATT v1.0 协议编解码、状态映射、错误码映射和 FSM→BLE 帧有单元/集成测试；
- PC 算法与固件算法有对拍测试，当前回归结果一致。

这些结果只说明当前回归基线，不代表真实硬件、整机性能或临床准确性。

## 未验证

- Pico 2W 板载 BLE 的 GATT 服务端、Indicate、CCCD、带响应 Write；
- APP 与 Pico 2W 的真机扫描/连接/订阅/控制/结果链路；
- 真实压力传感器、泵、阀、袖带和电源系统；
- 80 Hz 采样下的 BLE 发送阻塞时间、WDT 时序和整机稳定性；
- 电池、漏气、过压、传感器断连等真实故障路径。

## 未实现

- `RealDriver`：真实硬件驱动尚未接入；
- BLE 传输层：协议编解码已完成，GATT 服务端和 1 Hz 压力上报尚未实现；
- 设备端历史记录：按协议约定，历史由 APP 保存，设备不缓存、不补发。

## 已知问题

当前版本是开发快照，已知问题不止一个。具体 bug、复现步骤和修复状态统一在
GitHub Issues 中登记；本文件只维护整体边界，不代替 issue 列表。

提交新问题前，请先确认它属于：

- 协议/字节层：对照 `docs/interfaces/ble-gatt-protocol.md` v1.0；
- 控制层：`firmware/control_fsm.py`；
- 算法层：`firmware/analyzer.py` 或 `algo/bp/`；
- 驱动层：`firmware/drivers/`；
- 工具/测试：`firmware/tests/`、`algo/tests/`、`tools/`。
