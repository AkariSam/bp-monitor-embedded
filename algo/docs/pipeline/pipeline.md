# pipeline.py ＋ qc.py —— 端到端编排与质控

> 代码：`bp/pipeline.py`、`bp/qc.py`；规格：技术计划 §4.0（工作流）、§4.4（判废）。

## 1. 编排流程

```
run(rec, params) / run_csv(path, params)
  ① DEFLATE 切片（契约保证唯一连续段）；无段 → 拒绝「无 DEFLATE 段」
  ② 段长 ≤ 3×margin（1.5 s）→ 拒绝「DEFLATE 段过短」
  ③ filter_dc_ac：因果滤波 + 时基对齐
  ④ 两端裁 edge_margin（0.5 s）后进入分析
  ⑤ peaks.detect → ⑥ envelope.from_peaks + build
  ⑦ decision.decide → ⑧ qc.check
  ⑨ accepted = qc.ok ∧ SBP/DBP/MAP 齐全
```

`PipelineResult` 暴露全部过程量：`t_s / dc / ac / candidates / envelope /
decision / qc / n_candidates / n_accepted`——供出图与 PC↔C 对拍。

**禁读真值**：bp 包不含任何 truth/json 读取；真值只在 `scripts/run_pipeline.py
--eval` 与测试中读取。

## 2. 质控（qc.py）

| 检查 | 判据 | 实测（seed=1 接受场景） |
|---|---|---|
| 包络点数 | <8 判废 | 16–68 |
| 包络残差 CV（粗糙度） | `std(amp − amp_s)/mean(amp_s)` > 0.35 判废 | 0.022–0.064 |
| 离群峰占比 | Lim 离群 /（通过项 + 离群）> 0.3 判废 | 0（全部场景） |
| 交叉缺失 | 判定阶段理由直接并入 | truncated → 判废 |
| 运动伪影 | 未实现（仿真未建模；真实数据后补） | — |

## 3. 端到端实测表（seed=1，`run_pipeline.py --all --eval`）

| 场景 | 判定 | SBP err | DBP err | HR | 点数 | 备注 |
|---|---|---|---|---|---|---|
| normal | OK | +0.3 | +0.2 | 69.6 | 29 | |
| hypertension | OK | −0.1 | −0.6 | 69.6 | 45 | DIA 校验旗标（公式局限） |
| hypotension | OK | +0.0 | −0.8 | 69.6 | 21 | |
| wide_pp | OK | +0.7 | +0.8 | 69.6 | 49 | DIA 校验旗标（公式局限） |
| af | OK | +0.5 | +1.3 | 70.1 | 31 | IPP 单实现敏感（见 decision.md） |
| bradycardia | OK | +1.1 | −2.0 | 40.0 | 16 | 搏动网格最粗（4.5 mmHg/搏） |
| tachycardia | OK | +0.2 | +0.5 | 177.8 | 68 | HR 量化下限（−2.2） |
| truncated | **判废** | — | — | — | 21 | 「SBP 交叉未找到」✓ |

汇总：7/8 接受，MAE SBP 0.4 / DBP 0.9 mmHg（详细 JSON：
`out/eval/pipeline_eval.json`）。truncated 判废是**设计要求**（防静默错误）。
仿真形状与管线锚点对齐（2026-10-04，docs/sim/model.md §5.1）后，残余误差
收敛到搏动网格离散量级（≤2.0 mmHg）。
fs=40 冒烟通过（`--fs 40`，参数 `replace(fs=40)`）。

## 4. C 移植清单（W6 用）

固件按同一语义实现时需注意：

1. **滤波**：butter biquad 系数 PC 侧设计导出；因果 sosfilt 等价于 biquad
   级联；稳态初始化 = 按首样本预置状态（无此步则 DEFLATE 开头有暂态）。
2. **时基对齐**：DC 左移群延迟采样数、AC 左移相位延迟（负 = 右移）；
   系数与延迟均可离线标定后写 Flash。
3. **梯度**：因果分块（上一块统计），O(1) 内存；std 可用滑动和/平方和。
4. **有效性**：峰距与 prominence 因果；Lim 中心窗需 ≤4 搏的环形缓冲
   （~1 搏输出延迟）；中位数为小数组排序（n≤10）。
5. **包络**：int16 {P×10, A×100}；排序按 Pc（放气单调，天然有序）。
6. **判定**：抛物线细化/线性插值/中位数均为整型或软浮点可承载；
   比率参数走 Flash 可调。
7. **对拍**：同一 CSV 分别喂 PC 版与固件版，比较 `t_s/peaks/envelope/
   sbp/dbp/map/hr` 全链（scripts/coverify.py，W5–W6 实现）。

## 5. 已知简化 / 偏离（汇总入口）

各模块偏离详见各自 §5；管线级补充：

1. 未实现 §4.5 的「包络 <0.3×峰值提前转快排」——那是固件状态机职责
   （PC 端拿到的是完整 DEFLATE 段）。
2. 未实现饱和检测（≥95%FS）——固件职责，PC 无 FS 概念。
3. QC 的运动伪影门控缺口（与 sim deferred 清单对应）。

## 6. 图位（TODO）

- 图 pipeline-1：全链过程量一图流（p_raw+DC+AC+峰标记+包络+判定线），
  用 `PipelineResult` 直接出图，作为汇报主图。
- 图 pipeline-2：8 场景误差条形图（SBP/DBP 误差 vs 场景）。
