# -*- coding: utf-8 -*-
"""报告与可视化的冒烟测试（确保产物能生成且是合法 HTML）。"""

from __future__ import annotations

import html.parser

import pytest

from pysta import paths
from pysta import report as rep
from pysta import viz
from pysta.checks import EXAMPLES
from pysta.timing import Design, analyze


class _Collector(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.void = {"br", "hr", "img", "input", "meta", "link", "circle",
                     "line", "rect", "path", "polyline", "use"}

    def handle_starttag(self, tag, attrs):
        if tag not in self.void:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.void:
            return
        if not self.stack:
            self.errors.append(f"多余的 </{tag}>")
        elif self.stack[-1] != tag:
            self.errors.append(f"标签不匹配: 期待 </{self.stack[-1]}>，得到 </{tag}>")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


@pytest.fixture(scope="module")
def built():
    out = []
    for ex in EXAMPLES:
        d = Design.build(paths.DEFAULT_LIB,
                         paths.NETLIST_DIR / f"{ex.netlist_name}.v",
                         paths.SDC_DIR / f"{ex.sdc_name}.sdc")
        out.append((d, analyze(d)))
    return out


def test_report_is_text(built):
    text = rep.format_all(built)
    for _, res in built:
        assert res.design_name in text
    assert "data arrival time" in text
    assert "slack" in text


def test_report_handle_excluded_paths(built):
    """被 false path 屏蔽的路径也要在报告里说明原因，而不是静默消失。"""
    ex7 = next((d, r) for d, r in built if d.top == "ex7_async_cdc")
    text = rep.format_design(ex7[0], ex7[1], verbose_paths=5)
    assert "屏蔽" in text


def test_html_is_wellformed(built):
    doc = viz.render_html(built)
    p = _Collector()
    p.feed(doc)
    assert not p.errors, p.errors


def test_html_has_one_card_per_example(built):
    doc = viz.render_html(built)
    assert doc.count('<section class="card"') == len(built)


def test_html_ids_are_unique(built):
    import re
    doc = viz.render_html(built)
    ids = re.findall(r'<section class="card" id="([^"]+)"', doc)
    assert len(ids) == len(built)
    assert len(ids) == len(set(ids)), f"id 重复: {ids}"


def test_html_contains_waveform(built):
    doc = viz.render_html(built)
    assert doc.count("<svg class=\"wave") >= len(built) - 1
    assert "slack =" in doc
