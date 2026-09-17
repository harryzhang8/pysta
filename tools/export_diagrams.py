# -*- coding: utf-8 -*-
"""
导出独立 SVG 时序图
====================

把示例的时序波形单独导出成 ``.svg`` 文件，放进 ``docs/images/``，
供 README 和文档引用。

为什么用 SVG 而不是 PNG：

* 矢量图在任何缩放下都清晰，README 在手机 / 高分屏上都不会糊；
* 一个文件只有几 KB，不占仓库体积；
* 波形本来就是矢量画的，截屏反而会损失信息。

导出时会做两件事：

1. 把 ``width="100%"`` 换成固定像素尺寸，这样脱离 HTML 也能正确渲染；
2. 在图形最底层垫一个白色矩形 —— 否则 GitHub 深色主题下
   深灰色的文字会看不清。

用法::

    python tools/export_diagrams.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "src"))

from pysta import paths  # noqa: E402
from pysta.checks import EXAMPLES  # noqa: E402
from pysta.timing import Design, analyze  # noqa: E402
from pysta.viz import representative_path, timing_waveform  # noqa: E402

OUT_DIR = PROJ / "docs" / "images"
WIDTH = 1180.0

# 想导出哪些例子 -> 输出文件名
TARGETS: list[tuple[str, str]] = [
    ("ex6_multi_clk_out", "waveform-multiclock.svg"),
    ("ex9_multicycle_adder", "waveform-multicycle.svg"),
    ("ex9b_multicycle_adder_default_hold", "waveform-hold-violation.svg"),
    ("ex10_multicycle_mixed", "waveform-multicycle-mixed.svg"),
    ("ex2_in2reg", "waveform-in2reg.svg"),
]

_OPEN = re.compile(r'<svg class="wave[^"]*" viewBox="0 0 ([\d.]+) ([\d.]+)" width="100%"')


def standalone(svg: str) -> str:
    """把嵌入用的 SVG 转成可单独打开的 SVG（定尺寸 + 白底）。"""
    m = _OPEN.search(svg)
    if not m:
        raise ValueError("SVG 结构不符合预期，无法转换")
    w, h = float(m.group(1)), float(m.group(2))
    head = (f'<svg viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}" '
            f'role="img" xmlns="http://www.w3.org/2000/svg">')
    bg = f'<rect x="0" y="0" width="{w:.0f}" height="{h:.0f}" fill="#ffffff"/>'
    return _OPEN.sub(head + bg, svg, count=1)


def build(name: str):
    ex = next((e for e in EXAMPLES if e.name == name), None)
    if ex is None:
        raise SystemExit(f"找不到示例: {name}")
    design = Design.build(
        paths.DEFAULT_LIB,
        paths.NETLIST_DIR / f"{ex.netlist_name}.v",
        paths.SDC_DIR / f"{ex.sdc_name}.sdc",
    )
    return design, analyze(design)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, fname in TARGETS:
        try:
            design, res = build(name)
        except Exception as exc:  # noqa: BLE001
            print(f"  [跳过] {name}: {exc}")
            continue
        pt = representative_path(res)
        if pt is None:
            print(f"  [跳过] {name}: 没有可用路径")
            continue
        svg = standalone(timing_waveform(design, pt, width=WIDTH))
        dest = OUT_DIR / fname
        dest.write_text(svg, encoding="utf-8")
        total += 1
        print(f"  [写出] {fname:36s} {len(svg) / 1024:6.1f} KB  <- {name}")
    print(f"\n共导出 {total} 个 SVG -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
