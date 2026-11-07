"""批量生成仿真数据 → data/sim/。

用法（在 algo/ 目录下）：
  python scripts/gen_data.py --list
  python scripts/gen_data.py --scenario normal --seed 1
  python scripts/gen_data.py --all --seeds 1 2 3
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.generate import generate
from sim.scenarios import SCENARIOS

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "sim"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="示波法血压计仿真数据生成器")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS),
                        help="场景名（--list 查看全部）")
    parser.add_argument("--seed", type=int, default=1, help="随机种子（默认 1）")
    parser.add_argument("--all", action="store_true", help="生成全部场景")
    parser.add_argument("--seeds", type=int, nargs="+",
                        help="配合 --all 使用的 seed 列表")
    parser.add_argument("--list", action="store_true", help="列出全部场景名")
    parser.add_argument("--out", type=Path, default=DATA_DIR,
                        help=f"输出目录（默认 {DATA_DIR}）")
    args = parser.parse_args(argv)

    if args.list:
        for name in sorted(SCENARIOS):
            print(name)
        return 0

    if args.all:
        seeds = args.seeds or [args.seed]
        jobs = [(name, s) for name in sorted(SCENARIOS) for s in seeds]
    else:
        if args.scenario is None:
            parser.error("需指定 --scenario <名称> 或 --all（--list 查看场景名）")
        jobs = [(args.scenario, args.seed)]

    for name, seed in jobs:
        csv_path, truth_path = generate(name, seed, args.out)
        print(f"[ok] {csv_path.name} + {truth_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
