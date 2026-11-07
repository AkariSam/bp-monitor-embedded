# 算法管线文档（bp/）

> 对应代码：`algo/bp/` ＋ `scripts/run_pipeline.py` / `scripts/sweep.py`
> docstring 管「怎么用」，本目录管「为什么」——供开发、汇报与固件移植引用。

## 1. 定位：PC 侧唯一正本

- `bp/` 是示波法算法的**唯一正本**：固件（W6）将按同一算法语义移植，PC↔C 用
  同一 CSV 对拍防移植错误（技术实现计划 §4.0 工作流）。
- 管线**不读真值**：`.truth.json` 只在评估脚本/测试中读取。
- 输入契约唯一：`bp/csvio.py`（`t_ms,p_raw,phase`），仿真器与固件采集器同格式。
- 完成 W5「PC 端算法原型」里程碑：8 场景端到端真值对照（seed=1）7/8 接受
  （truncated 按设计判废），MAE SBP 0.4 / DBP 0.9 mmHg（仿真形状锚定后，2026-10-04）。

## 2. 总体流水线

```
契约 CSV
  ↓ DEFLATE 切片（两端裁 0.5 s）
  ↓ filters：DC（0.5 Hz 低通） / AC（0.5–8 Hz 四阶带通）
     因果 sosfilt + 首样本稳态初始化 + 时基对齐（DC 群延迟 / AC 相位延迟）
  ↓ peaks：双门限滞回检测 + 动态梯度 → 三层有效性（峰距/prominence/邻搏）
  ↓ envelope：逐搏 (Pc, ptp) → 按压力排序 → 3 中值 + 3 滑动平均
  ↓ decision：MAP（抛物线细化）→ SBP/DBP（0.50/0.70 比率交叉）→ HR/DIA/IPP
  ↓ qc：点数 / 残差 CV / 离群占比 / 交叉缺失 → 接受或判废（附理由）
结果 + 全部过程量（供出图与对拍）
```

## 3. 模块地图与阅读顺序

| 文档 | 对应模块 | 内容 |
|---|---|---|
| [filters.md](filters.md) | `bp/filters.py` | 因果滤波、启动初始化、群延迟 vs 相位延迟（对拍要点） |
| [peaks.md](peaks.md) | `bp/peaks.py` | 滞回状态机、动态梯度归约（GCY th=dc×CV%×3 的推导与偏离）、三层有效性 |
| [envelope.md](envelope.md) | `bp/envelope.py` | 逐搏对定义（ptp 窗 / 峰时刻 DC）、排序与平滑 |
| [decision.md](decision.md) | `bp/decision.py` | 比率法、MAP 细化、HR/IPP/DIA 校验、系统偏移实测 |
| [pipeline.md](pipeline.md) | `bp/pipeline.py` ＋ `bp/qc.py` | 编排、质控判废、端到端实测表、C 移植清单 |
| [robustness.md](robustness.md) | `scripts/sweep.py` | 参数扫描四实验（2026-10-11）：参数分级、噪声判据、比率换算率、仿真器参数边界 |
| [robustness_explained.md](robustness_explained.md) | — | 上述实验的通俗版讲解（非技术向，汇报可用） |

建议顺序：filters → peaks → envelope → decision → pipeline。

## 4. 快速使用

```bash
# 在 algo/ 目录下（先激活 .venv）
python scripts/run_pipeline.py --scenario normal --seed 1    # 单场景
python scripts/run_pipeline.py --all --eval                  # 8 场景误差表 + out/eval/
python scripts/run_pipeline.py --csv data/sim/af_s001.csv    # 任意契约 CSV
python scripts/run_pipeline.py --all --eval --fs 40          # 采样率扫描（E1 铺路）
python scripts/sweep.py --mode algo                          # 参数扫描四模式
# algo / stress / ratio / sim，结果 → out/eval/sweep_*.json + out/figs/sweep_*.png
```

## 5. 参数管理

全部参数收敛在 `bp/params.py` 的 `AlgoParams`（frozen dataclass），默认值出自
技术计划 §4.2–4.4；实验用 `dataclasses.replace` 覆盖单项。该表是未来固件
Flash 参数表的原型。

## 6. 文档-代码锚点

每篇文档含「实现映射」与「已知简化/偏离」节。**改代码必须同步对应文档节**；
与规格的每一处偏离必须在本目录留痕（当前清单见各篇 §5）。
