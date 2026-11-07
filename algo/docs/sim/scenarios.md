# 场景库（sim/scenarios.py）

> 对应代码：`sim/scenarios.py`。8 个命名场景 = 参数覆盖（`dataclasses.replace`），默认值即 normal 基线。

## 1. 场景总表

| 场景 | 覆盖参数 | 物理设定 | 测试意图（考验管线哪条路径） |
|---|---|---|---|
| normal | — | 120/80 @ 70 bpm | 基线全链路 |
| hypertension | sbp=160, dbp=100 | 包络右移 | 高压端比率行为；高预充 |
| hypotension | sbp=90, dbp=60 | 包络左移 | 低压端；P<40 快排边界 |
| wide_pp | sbp=150, dbp=70 | 脉压 80 | 比率-真值敏感性（0.50/0.70 漂移最敏感） |
| af | beat_jitter=0.2 | RR 间期 ±20% 抖动 | 峰有效性剔除、包络离群、IPP 节律提示 |
| bradycardia | hr=40 | 搏动稀疏 | 包络点数下限（<8 判废路径；配快放气可组合） |
| tachycardia | hr=180 | 搏动密集 | 峰距下限 300 ms、相邻峰幅比校核 |
| truncated | p_target=105 | 预充不足（<SBP） | 高压侧包络截断 → SBP 低估/判废路径 |

## 2. 扩展方法

新场景 = 注册表加一行 + 一个 `replace` 覆盖：

```python
def custom():
    return replace(PhysioParams(), deflate_rate=5.0, hr=40.0), ArtifactParams()
```

组合场景（如 bradycardia + 快放气 → 触发包络欠采样判废）直接在测试里 `replace` 构造，不必注册。注册表 `SCENARIOS` 供 CLI `--scenario/--all/--list` 使用。

## 3. 说明

- 场景覆盖参数空间，用于算法健壮性回归；**不代表**临床人群分布。
- 对照试验（T7）的误差预算分析以真实人群为准。
