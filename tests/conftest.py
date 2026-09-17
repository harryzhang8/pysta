# -*- coding: utf-8 -*-
"""让 `pytest` 在没有 `pip install -e .` 的情况下也能直接跑（src 布局）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
