"""pytest 引导：确保 algo/ 在 sys.path 上（使 ``import bp`` / ``import sim`` 可用）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
