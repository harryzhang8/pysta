# -*- coding: utf-8 -*-
"""
pysta —— 纯 Python 的迷你静态时序分析（STA）工具链
==================================================

模块划分::

    liberty.py   Liberty (.lib) 解析 —— 单元延时 / 建立 / 保持 / CP->Q
    netlist.py   门级网表（结构化 Verilog）解析
    sdc.py       SDC / Tcl 约束解析（含 expr 求值与 get_ports/get_pins 查询）
    timing.py    时序图构建 + 路径枚举 + slack 计算
    report.py    时序报告输出（report_timing 风格）
    viz.py       时序波形 / slack 可视化（自包含 HTML + SVG）
    checks.py    示例的期望值断言表

设计目标是**零第三方依赖**：只用 Python 标准库就能跑通
"工艺库 + 门级网表 + SDC 约束 -> 建立/保持时序分析" 这条链路。
"""

from .liberty import Cell, Library, Table, parse_liberty, parse_liberty_file
from .netlist import Instance, Module, parse_verilog, parse_verilog_file
from .sdc import (
    ClockSpec,
    ObjectQuery,
    SdcDatabase,
    parse_sdc,
    parse_sdc_file,
)
from .timing import AnalysisResult, Design, PathTiming, TimingPath, analyze, lcm_frac

__version__ = "0.1.0"

__all__ = [
    "Cell", "Library", "Table", "parse_liberty", "parse_liberty_file",
    "Instance", "Module", "parse_verilog", "parse_verilog_file",
    "ClockSpec", "ObjectQuery", "SdcDatabase", "parse_sdc", "parse_sdc_file",
    "AnalysisResult", "Design", "PathTiming", "TimingPath", "analyze", "lcm_frac",
    "__version__",
]
