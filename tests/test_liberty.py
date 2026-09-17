# -*- coding: utf-8 -*-
"""Liberty 解析器测试。"""

from __future__ import annotations

from fractions import Fraction

import pytest

from pysta import paths
from pysta.liberty import Table, parse_liberty, parse_liberty_file


@pytest.fixture(scope="module")
def lib():
    return parse_liberty_file(paths.DEFAULT_LIB)


def test_library_header(lib):
    assert lib.name == "tt_simple"
    assert lib.time_unit == "1ns"
    assert lib.capacitive_load_unit == "1,pf"


def test_cells_present(lib):
    for name in ("DFFRQ", "INVX1", "NAND2X1", "MUX2X1", "ADDFA", "MULT8X8", "CLKBUF"):
        assert name in lib.cells, f"缺少单元 {name}"


def test_dff_constraints(lib):
    dff = lib.cell("DFFRQ")
    assert dff.is_sequential
    assert dff.clock_pin == "CP"
    assert dff.next_state_pin == "D"
    assert dff.setup == Fraction(1)
    assert dff.hold == Fraction(1, 2)


def test_dff_clk_to_q_is_load_dependent(lib):
    dff = lib.cell("DFFRQ")
    # 空载 0.9ns，负载 0.05pf 时 1.0ns（表格的第二个点）
    assert dff.clk_to_q.lookup(Fraction(1, 50), Fraction(0)) == Fraction(9, 10)
    assert dff.clk_to_q.lookup(Fraction(1, 50), Fraction(1, 20)) == Fraction(1)
    # 扇出为 2 时负载 0.10pf，按线性外推得到 1.1ns
    assert dff.clk_to_q.lookup(Fraction(1, 50), Fraction(1, 10)) == Fraction(11, 10)


def test_pin_directions_and_capacitance(lib):
    inv = lib.cell("INVX1")
    assert inv.pins["A"].direction == "input"
    assert inv.pins["Y"].direction == "output"
    assert inv.pins["A"].capacitance == Fraction(1, 20)


def test_comb_arc_lookup(lib):
    inv = lib.cell("INVX1")
    arc = inv.comb_arc_from("A")
    assert arc is not None and arc.output_pin == "Y"
    assert arc.delay(Fraction(1, 50), Fraction(1, 20)) == Fraction(3, 50)   # 0.06


def test_mux_has_three_input_arcs(lib):
    mux = lib.cell("MUX2X1")
    assert mux.pins["Y"].function == "(I0*!S)+(I1*S)"
    assert {a.related_pin for a in mux.arcs} == {"I0", "I1", "S"}


# ---------------------------------------------------------------- Table 本身


def test_table_constant():
    t = Table.constant(Fraction(1, 4))
    assert t.lookup(0, 0) == Fraction(1, 4)
    assert t.lookup(99, 99) == Fraction(1, 4)


def test_table_bilinear_interpolation():
    t = Table([Fraction(0), Fraction(1)], [Fraction(0), Fraction(1)],
              [[Fraction(0), Fraction(1)], [Fraction(1), Fraction(2)]])
    assert t.lookup(Fraction(0), Fraction(0)) == 0
    assert t.lookup(Fraction(1, 2), Fraction(1, 2)) == 1
    assert t.lookup(Fraction(1, 2), Fraction(0)) == Fraction(1, 2)


def test_table_extrapolates_outside_range():
    t = Table([Fraction(0)], [Fraction(0), Fraction(1)],
              [[Fraction(1), Fraction(2)]])
    # x = 2 在 [0,1] 之外，按同样斜率外推 => 3
    assert t.lookup(Fraction(0), Fraction(2)) == 3


def test_syntax_errors_are_reported():
    with pytest.raises(Exception):
        parse_liberty("library (x) { cell (a) { pin (P) { direction : input } }")


def test_complex_attribute_without_braces():
    """``capacitive_load_unit (1, pf);`` 这种没有花括号的复合属性要能解析。"""
    lib = parse_liberty(
        'library (x) {\n'
        '  capacitive_load_unit (1, pf);\n'
        '  cell (C) { pin (A) { direction : input; } }\n'
        '}\n')
    assert lib.capacitive_load_unit == "1,pf"
    assert "C" in lib.cells
