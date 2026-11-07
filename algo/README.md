# 算法工作区（PC 优先工作流）

> 负责人：算法岗（组长）｜方法论依据：技术实施计划 §4.0「算法 PC 优先开发」
> 范围：仿真器 ＋ PC 算法管线 ＋ C 移植对拍 ＋ 数据 ＋ 产物；整目录可拷贝给队友复用。
> 当前状态（2026-10-11）：**仿真器 ＋ PC 算法管线 ＋ 参数扫描均已完成**（94 项测试全绿，
> 8 场景真值对照 7/8 接受、MAE SBP 0.4/DBP 0.9 mmHg——仿真形状已锚定到
> 管线常数 0.50/0.70/0.41；四模式参数扫描已跑，参数分级与硬件判据见
> docs/pipeline/robustness.md）；`c/` 对拍待 W5–W6。

## 目录地图

| 路径 | 内容 |
|---|---|
| `bp/` | Python 算法包（管线唯一正本）：csvio / params / filters / peaks / envelope / decision / qc / pipeline（全部已实现） |
| `sim/` | 仿真器：model（生理模型）/ artifacts（噪声伪影）/ scenarios（场景档位）/ generate（生成入口） |
| `scripts/` | CLI 入口（薄壳）：gen_data / plot_data / plot_spectrum / run_pipeline / fit_shape / sweep / coverify |
| `c/` | C 移植（固件算法上游正本；W5–W6 对拍后交嵌入式侧收编进固件） |
| `tests/` | pytest：单模块测试 ＋ fixtures 回归 |
| `data/sim/` | 仿真数据（可重建，不入库）：`<scenario>_s<seed>.csv` ＋ 同名 `.truth.json` |
| `data/real/` | 实测数据（采集器导出，不入库）：`YYYYMMDD_HHMM_<tag>/raw.csv` |
| `data/fixtures/` | 入库：固定小样本（回归/对拍/演示） |
| `out/` | 产物（不入库）：`figs/` 图、`eval/` 误差评估；选优后拷进报告 |

## 数据契约（W5 与嵌入式侧冻结对象）

管线唯一输入 = 固件「数据采集器」输出格式；**唯一权威实现 `bp/csvio.py`**：

```
t_ms,p_raw,phase
0.0,128.35,INFLATE
12.5,128.30,INFLATE
```

- `t_ms`：浮点毫秒（0.0, 12.5, 25.0, …），严格递增（80 Hz 采样网格）
- `p_raw`：袖带压 [mmHg]，两位小数（量化后）
- `phase` ∈ INFLATE / SETTLE / DEFLATE / DUMP
- 真值旁车（仅仿真）：同名 `.truth.json`——**管线禁止读取真值**，只允许评测脚本使用
- 详细规则与复现机制：[docs/sim/generate.md](docs/sim/generate.md)

## 怎么跑

```bash
cd algo
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
python scripts/gen_data.py --list                       # 查看场景
python scripts/gen_data.py --scenario normal --seed 1   # 仿真数据 → data/sim/
python scripts/plot_data.py --all --seeds 1             # 波形图 → out/figs/
python scripts/plot_spectrum.py --all --seeds 1        # AC 频域分析 → out/figs/*_spec.png
python scripts/run_pipeline.py --all --eval             # 全管线 + 真值误差表 → out/eval/
python scripts/sweep.py --mode algo                     # 参数扫描（algo/stress/ratio/sim）
python -m pytest tests/ -v                              # 回归测试（94 项）
```

## 仿真器（已实现）

原理文档：[docs/sim/](docs/sim/README.md)（模型推导 / 噪声 / 场景 / 复现规则）

场景：normal、hypertension、hypotension、wide_pp、af、bradycardia、tachycardia、truncated

| 主题 | 一句话摘要 | 详见 |
|---|---|---|
| 信号模型 | B+μ 非对称高斯顺应性积分 → 钟形包络（峰值≈0.41·S+0.59·D）；双高斯脉搏波；DC 四段斜坡 | docs/sim/model.md |
| 形状锚定 | fit_shape.py 求解 3 参数，精确命中 r_s=0.50 / r_d=0.70 / k=0.41；3 根可辨识性记录在案 | docs/sim/model.md §5.1 |
| 噪声 | 白噪（密集网格加，抽样自然混叠）/ 呼吸（骑 0.5 Hz 边界）/ 量化 LSB=0.1 | docs/sim/artifacts.md |
| 未建模 | 运动伪影、泵阀瞬态（deferred 清单 + 补做时机） | docs/sim/artifacts.md §5 |
| 随机复现 | SeedSequence.spawn 三流域；同 (scenario, seed) 字节级复现 | docs/sim/generate.md §2 |

## 算法管线（已实现）

原理文档：[docs/pipeline/](docs/pipeline/README.md)（滤波/峰检测/包络/判定/质控逐模块推导）

流程：DEFLATE 切片 → DC/AC 因果滤波与时基对齐 → 双门限滞回峰检测 + 三层有效性
→ 逐搏包络 → 比率判定（0.50/0.70）→ 质控判废。

| 主题 | 一句话摘要 | 详见 |
|---|---|---|
| 滤波联动 | DC 群延迟（36 采样）与 AC 相位延迟（−3.3 采样，超前）分路补偿；首样本稳态初始化 | docs/pipeline/filters.md |
| 动态梯度 | GCY `th=dc×CV%×3` 归约为 1.0×块标准差（3σ 大于峰谷差会卡死滞回）；CV 门实测无区分度 → 三层防御替代 | docs/pipeline/peaks.md |
| 包络 | Pc 取峰时刻 DC、A 取搏动中点窗 ptp；3 中值 + 3 滑动平均 | docs/pipeline/envelope.md |
| 判定 | MAP 抛物线细化；比率交叉从 MAP 向外首交；HR 中位数（fs=80 量化下限已记录） | docs/pipeline/decision.md |
| 质控 | 点数 <8 / 残差 CV >0.35 / 离群占比 >0.3 / 交叉缺失 → 判废附理由 | docs/pipeline/pipeline.md |
| 实测 | 7/8 接受（truncated 判废✓），MAE SBP 0.4 / DBP 0.9 mmHg（形状对齐后） | out/eval/pipeline_eval.json |
| 健壮性 | 四实验参数扫描：噪声判据 σ≤0.1 mmHg、峰幅工作下限 ≈1.5、E3 漏气 2–4 mmHg/s | docs/pipeline/robustness.md |

## 版本控制

- 入库：代码 / fixtures / README / requirements
- 不入库：`data/sim/`、`data/real/`、`out/`、`.venv/`、缓存（见仓库根 `.gitignore`）

## 里程碑映射

| 时间 | 本目录交付 |
|---|---|
| W3–W4 | ✅ 仿真器（sim + csvio + 33 项测试）；✅ PC 管线（bp 全模块 + 52 项新增测试） |
| W5 | ✅ PC 算法原型提前完成（8 场景真值对照 + 误差表）；✅ 仿真形状锚定对齐（2026-10-04）；✅ 参数扫描四实验完成（2026-10-11，robustness.md）；csvio 定版、首批 fixtures 提交、与嵌入式侧冻结 CSV 格式 |
| W5–W6 | c/ ＋ coverify：PC↔C 对拍；C 正本交 B |
| W6+ | data/real/ 接入同一管线；仿真退役为 tests 回归 |
