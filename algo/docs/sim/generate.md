# 生成入口与复现规则（sim/generate.py）

> 对应代码：`sim/generate.py`、`bp/csvio.py`、`scripts/gen_data.py`。

## 1. 生成流水线

```
scenarios.get_scenario(name) → (PhysioParams, ArtifactParams)
        ↓
SeedSequence(seed).spawn(3) → physio / noise / breath 三个独立随机流
        ↓
model.synthesize（密集网格 1000 Hz）          ← rng=physio
        ↓
artifacts.apply_artifacts（密集网格）          ← rng=noise, rng=breath
        ↓
sample_grid → 80 Hz 输出网格（最近邻索引）
        ↓
quantize(LSB=0.1) ＋ bp.csvio.write_csv
        ↓
<scenario>_s<seed>.csv ＋ <scenario>_s<seed>.truth.json
```

## 2. 随机数分流域（为什么不能共用一个流）

**问题**：若所有模块共用一个随机流，任何模块增减一次随机调用，下游模块的抽数序列整体平移——"同 seed"名存实亡（例如改动 motion 模块会让 noise 输出全变）。

**方案**：`np.random.SeedSequence(seed).spawn(n)` 派生**独立流域**，按名分发：

| 流域 | 消费者 | 抽什么 |
|---|---|---|
| physio | `model.beat_times` | RR 间期抖动 |
| noise | `artifacts.add_noise` | 白高斯噪声 |
| breath | `artifacts.add_breathing` | 呼吸初相 |

**冻结警告**：`RNG_STREAMS` 的名称与顺序是复现契约的一部分，一旦发布不得改动（否则同 seed 不再字节级复现）。新增流域只在列表**末尾**追加。

## 3. CSV 契约（W5 与嵌入式侧冻结对象）

```
t_ms,p_raw,phase
0.0,0.50,INFLATE
12.5,0.60,INFLATE
...
66162.5,-0.10,DUMP
```

| 列 | 类型 | 规则 |
|---|---|---|
| t_ms | 浮点毫秒，1 位小数 | 0.0, 12.5, 25.0, …；严格递增（80 Hz 网格） |
| p_raw | mmHg，2 位小数 | 量化后取值 |
| phase | 枚举 | INFLATE / SETTLE / DEFLATE / DUMP |

**唯一权威**：`bp/csvio.py`（`write_csv` / `read_csv` / `validate`；`validate` 支持 `dt_ms` 覆盖以适配采样率扫描，如 40 Hz → 25.0）。管线其余模块禁止直接触碰 CSV 文本。

## 4. 真值 JSON（旁车，管线禁止读取）

```json
{
  "scenario": "normal", "seed": 1, "fs_out": 80.0,
  "sbp": 120.0, "dbp": 80.0, "hr": 70.0,
  "map": 96.4, "shape_resolved": {"sigma_n": 2.337, "sigma_p": 31.223, "mu": -13.614},
  "physio": { "...": "全部解析后参数（含默认值）" },
  "artifacts": { "...": "同上" }
}
```

- `map` = 模型包络峰值压力（数值解 ≈ 0.41·SBP+0.59·DBP），用于包络 sanity 检查（非临床 MAP）。
- **全参数回显**：truth 记录生成时的完整参数集（含默认值解析结果）→ 每个数据文件自描述、可追溯、扫描实验可复现。参数默认值以代码 dataclass 为唯一权威。

## 5. 复现规则

1. 同 `(scenario, seed)` → CSV 与 truth **字节级一致**（测试守护）。
2. 随机性全部来自 spawn 流域；禁止使用全局 `np.random`。
3. 采样保真度：80 Hz 输出 vs 1000 Hz 参考的逐搏振幅误差 ≤5%（测试守护，≥20 搏）。

## 6. 实现映射

| 函数 | 签名 | 职责 |
|---|---|---|
| `sample_grid` | `(t_dense, fs_in, fs_out) -> (t_ms, idx)` | 密集→输出网格索引映射 |
| `generate` | `(scenario, seed, outdir, fs_out=80) -> (csv, truth)` | 全流程编排 |
| `RNG_STREAMS` | `("physio", "noise", "breath")` | 流域清单（冻结） |

## 7. 图位（TODO）

- [ ] 图 6：生成流水线示意
- [ ] 图 7：80 Hz 采样 vs 1000 Hz 参考（DEFLATE 段局部对比）
