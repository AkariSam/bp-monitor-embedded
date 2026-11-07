# 仿真器文档（sim/）

> 对应代码：`algo/sim/` ＋ `bp/csvio.py` ＋ `scripts/gen_data.py`
> docstring 管「怎么用」，本目录管「为什么」——供开发、汇报与后续维护引用。

## 1. 定位：开发主干 + 测试夹具

- 硬件与实测数据未就绪期间，仿真提供**已知真值 + 可控边界场景**，让 PC 算法管线先于实物开发（W5/W6 里程碑不依赖实物数据）。
- 仿真验证**管线正确性**（实现无误、参数可调、边界可复现），不验证**生理准确性**——比率 0.50/0.70 最终只能由实测标定。
- 红线：仿真器代码量不得超过管线本体；真实数据到位后，仿真退役为回归测试。
- 真值旁车 `.truth.json` 只允许评测脚本读取，**管线禁止读真值**。

## 2. 两级时间网格与生成流水线

```
生理模型（fs_sim = 1000 Hz 密集参考网格，扮演「连续过程」）
   ↓  注入白噪声 / 呼吸（仍在密集网格 → 抽样时自然混叠）
采样器（抽到 fs_out = 80 Hz，与固件采集器契约一致）
   ↓  量化（LSB = 0.1 mmHg）
CSV（t_ms 浮点毫秒）＋ 真值 JSON
```

密集网格的价值：80 Hz 采样损失可被**量化测量**（采样保真度测试 ≤5%，见 generate.md）。

## 3. 模块地图与阅读顺序

| 文档 | 对应模块 | 内容 |
|---|---|---|
| [model.md](model.md) | `sim/model.py` | B+μ 非对称高斯顺应性→振荡包络（锚定 0.50/0.70/0.41）、双高斯脉搏波、DC 四段斜坡；**统一符号表** |
| [artifacts.md](artifacts.md) | `sim/artifacts.py` | 白噪声/呼吸/量化；已识别未建模清单（运动伪影、泵阀瞬态） |
| [scenarios.md](scenarios.md) | `sim/scenarios.py` | 8 场景参数表 + 每个场景的测试意图 |
| [generate.md](generate.md) | `sim/generate.py` | 流水线、随机数分流域、CSV/truth 契约、复现规则 |

建议顺序：model → artifacts → scenarios → generate。

## 4. 快速使用

```bash
# 在 algo/ 目录下（先激活 .venv）
python scripts/gen_data.py --list                      # 列出场景
python scripts/gen_data.py --scenario normal --seed 1  # 单场景
python scripts/gen_data.py --all --seeds 1 2 3         # 全场景 × 多 seed
python scripts/plot_data.py --scenario normal --seed 1 # 波形图 → out/figs/
python scripts/plot_data.py --all --seeds 1            # 全场景波形图
python scripts/plot_spectrum.py --all --seeds 1        # AC 频域分析图 → out/figs/*_spec.png
```

输出：`data/sim/<scenario>_s<seed>.csv` ＋ 同名 `.truth.json`（gitignored）；
波形图：`out/figs/<scenario>_s<seed>.png`（三面板：全览 / DEFLATE 放大 / 包络粗算）。
包络为**可视化近似**（切片滤波＋峰检测），正式判定以 `bp/` 管线为准。

## 5. 文档-代码锚点

每篇文档含「实现映射」节（函数签名清单）。**改代码必须同步对应文档节**，防止文档腐烂。
