# -*- coding: utf-8 -*-
"""示例级回归测试：把 `checks.py` 里的期望值全部跑一遍。

这是整个仓库的**总闸**：任何一个示例被改坏（改错约束、改错网表、
引擎算错），这里都会红。
"""

from __future__ import annotations

import pytest

from pysta import paths
from pysta.checks import EXAMPLES, Example
from pysta.timing import Design, analyze


def build(ex: Example):
    design = Design.build(
        paths.DEFAULT_LIB,
        paths.NETLIST_DIR / f"{ex.netlist_name}.v",
        paths.SDC_DIR / f"{ex.sdc_name}.sdc",
    )
    return design, analyze(design)


def _cases():
    for ex in EXAMPLES:
        for chk in ex.checks:
            yield pytest.param(ex, chk, id=f"{ex.name}::{chk.what[:48]}")


@pytest.mark.parametrize("ex,chk", list(_cases()))
def test_expectation(ex, chk):
    design, res = build(ex)
    actual = chk.show_actual(design, res)
    assert chk.ok(design, res), (
        f"{ex.name}: {chk.what}\n"
        f"  期望 {chk.show()}，实际 {actual}\n"
        f"  {chk.detail}")


@pytest.mark.parametrize("ex", EXAMPLES, ids=[e.name for e in EXAMPLES])
def test_example_builds(ex):
    design, res = build(ex)
    assert res.paths, f"{ex.name} 没有分析出任何路径"
    assert design.sdc.clocks, f"{ex.name} 的 SDC 里没有时钟"


@pytest.mark.parametrize("ex", EXAMPLES, ids=[e.name for e in EXAMPLES])
def test_example_files_exist(ex):
    assert (paths.NETLIST_DIR / f"{ex.netlist_name}.v").exists()
    assert (paths.SDC_DIR / f"{ex.sdc_name}.sdc").exists()


def test_every_netlist_has_sdc():
    """每个网表都应当有同名（或被显式引用）的约束文件。"""
    used = {e.netlist_name for e in EXAMPLES}
    for v in sorted(paths.NETLIST_DIR.glob("*.v")):
        assert v.stem in used, f"netlist/{v.name} 没有被任何示例引用"


def test_every_sdc_is_used():
    used = {e.sdc_name for e in EXAMPLES}
    for s in sorted(paths.SDC_DIR.glob("*.sdc")):
        assert s.stem in used, f"sdc/{s.name} 没有被任何示例引用"


def test_every_rtl_has_netlist():
    """每个 RTL 都应当有对应的门级网表（除了 ex9b，它复用 ex9 的网表）。"""
    netlists = {p.stem for p in paths.NETLIST_DIR.glob("*.v")}
    for v in sorted(paths.RTL_DIR.glob("*.v")):
        assert v.stem in netlists, f"rtl/{v.name} 没有对应的门级网表"


def test_no_unexpected_setup_violation():
    """除了刻意设计的反例，其它示例都不应该有时序违例。"""
    for ex in EXAMPLES:
        if ex.name == "ex9b_multicycle_adder_default_hold":
            continue        # 这个示例就是故意制造保持违例的
        _, res = build(ex)
        bad = [p for p in res.violations()]
        assert not bad, f"{ex.name} 出现意外违例: " + ", ".join(
            f"{p.path.startpoint}->{p.path.endpoint} slack={p.slack_setup}" for p in bad)
