# -*- coding: utf-8 -*-
"""示例资源的位置。

示例随包一起分发（`package-data` 里包含 `examples/**`），
所以 `pip install pysta` 之后 `pysta run` 也能直接找到它们。
"""

from __future__ import annotations

from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"
LIB_DIR = EXAMPLES_DIR / "lib"
NETLIST_DIR = EXAMPLES_DIR / "netlist"
SDC_DIR = EXAMPLES_DIR / "sdc"
RTL_DIR = EXAMPLES_DIR / "rtl"

DEFAULT_LIB = LIB_DIR / "tt_simple.lib"

__all__ = ["EXAMPLES_DIR", "LIB_DIR", "NETLIST_DIR", "SDC_DIR", "RTL_DIR", "DEFAULT_LIB"]
