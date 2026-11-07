# 智能电子血压计（嵌入式侧）

> **状态：WIP（2026-11-07 公开快照）**
>
> 当前代码未完成 Pico 2W 真机 BLE 验证，未做整机验收。248 项测试通过只代表当前回归基线，
> 不代表稳定、可发布或可临床使用。具体未完成项和已知问题见 [`docs/status.md`](docs/status.md)。

基于 Raspberry Pi Pico 2W + MicroPython 的示波法血压计嵌入式工程，包含：

- `firmware/`：控制状态机、血压算法、驱动接口、FakeDriver、PC 侧测试与演示；
- `algo/`：PC 算法参考实现、仿真器、测试与数据生成脚本；
- `docs/interfaces/ble-gatt-protocol.md`：BLE GATT v1.0 设计冻结正本；
- `docs/history.md`：项目历史节点；
- `docs/status.md`：已验证 / 未验证 / 已知边界。

APP 在独立仓库维护，本仓库不包含 APP、APK 或手机端代码。

## 快速开始

需要 Python 3.12+。在仓库根目录执行：

```bash
python -m venv .venv
source .venv/bin/activate          # Windows Git Bash: source .venv/Scripts/activate
pip install -r algo/requirements.txt

# 生成测试用的仿真数据（不提交到仓库）
python algo/scripts/gen_data.py --all --seeds 1

# 全量回归
python -m pytest firmware/tests algo/tests -q

# 两个演示
python firmware/demo/run_demo.py
python firmware/demo/run_algo_demo.py
```

`firmware/` 的目标运行时是 MicroPython；上面的命令用于 PC 侧回归和演示，不需要真实硬件。

## 目录

```text
firmware/       Pico 2W 固件、控制层、算法、驱动接口、测试
algo/           PC 算法参考、仿真器、脚本、测试
docs/           技术计划、GATT 协议、历史节点、状态说明
tools/          公开仓库检查脚本
```

## 分支约定

- `main`：公开 WIP 快照 + README；
- `dev`：后续集成分支；
- `dev-zmy`、`dev-yfy`：个人开发分支。

`main` 当前不是稳定版本，不创建 GitHub Release；版本标签使用
`snapshot-YYYY-MM-DD-wip`，验收通过后再使用 `v0.x`。

## 协议边界

BLE 协议以 [`docs/interfaces/ble-gatt-protocol.md`](docs/interfaces/ble-gatt-protocol.md)
v1.0 为准。APP 侧按同一份文档实现；本仓库不维护平行帧格式。

## 安全边界

这是课程项目原型，不是医疗器械。不要用于诊断、治疗或任何临床决策。
泵、阀、袖带和压力传感器必须按硬件安全规范连接；上电默认态必须满足泵停、阀失电、袖带可泄压。

## 历史

本仓库采用全新历史。早期开发节点见 [`docs/history.md`](docs/history.md)，旧仓库和旧 commit
不公开。
