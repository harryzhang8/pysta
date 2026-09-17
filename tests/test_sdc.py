# -*- coding: utf-8 -*-
"""SDC / Tcl 解析器测试。"""

from __future__ import annotations

from fractions import Fraction

import pytest

from pysta import paths
from pysta.sdc import ObjectQuery, SdcError, eval_expr, parse_sdc, parse_sdc_file


@pytest.fixture
def query():
    q = ObjectQuery()
    q.ports = {"clk", "A", "B", "C", "D"}
    q.pins = {"FF1/CP", "FF2/D", "FF3[0]/D", "FF3[1]/D", "MUX0/I0", "MUX1/I1"}
    return q


# ------------------------------------------------------- expr 与 Tcl 语义


def test_expr_plain_arithmetic():
    assert eval_expr("10").value == 10
    assert eval_expr("2+3*4").value == 14
    assert eval_expr("(2+3)*4").value == 20


def test_expr_integer_division_truncates():
    """Tcl 的整数除法：1/75 得到 0，这是 SDC 里非常容易踩的坑。"""
    assert eval_expr("1/75*1000").value == 0
    assert eval_expr("1/75*1000").is_int


def test_expr_real_division_is_exact():
    """1.0/75*1000 应当得到精确的 40/3，而不是浮点的 13.333333。"""
    num = eval_expr("1.0/75*1000")
    assert num.value == Fraction(40, 3)
    assert not num.is_int


def test_expr_div_by_zero():
    with pytest.raises(SdcError):
        eval_expr("1.0/0")


# ------------------------------------------------------------- create_clock


def test_create_clock_on_port(query):
    db = parse_sdc("create_clock -period 10 [get_ports clk]", query)
    clk = db.clocks["clk"]
    assert clk.period == 10
    assert clk.source == "clk"
    assert clk.waveform == [Fraction(0), Fraction(5)]
    assert not clk.is_virtual


def test_create_virtual_clock(query):
    db = parse_sdc("create_clock -name clka -period 30", query)
    assert db.clocks["clka"].is_virtual
    assert db.clocks["clka"].period == 30


def test_create_clock_requires_period(query):
    with pytest.raises(SdcError):
        parse_sdc("create_clock [get_ports clk]", query)


def test_custom_waveform(query):
    db = parse_sdc("create_clock -period 10 -waveform {0 4} [get_ports clk]", query)
    assert db.clocks["clk"].waveform == [Fraction(0), Fraction(4)]


def test_virtual_clock_requires_name(query):
    with pytest.raises(SdcError):
        parse_sdc("create_clock -period 10", query)


# ------------------------------------------------------------- 时钟属性


def test_clock_uncertainty_setup_only(query):
    db = parse_sdc(
        "create_clock -period 10 [get_ports clk]\n"
        "set_clock_uncertainty -setup 0.5 [get_clocks clk]\n", query)
    assert db.clocks["clk"].unc_setup == Fraction(1, 2)
    assert db.clocks["clk"].unc_hold == 0


def test_clock_uncertainty_both_when_no_flag(query):
    db = parse_sdc(
        "create_clock -period 10 [get_ports clk]\n"
        "set_clock_uncertainty 0.3 [get_clocks clk]\n", query)
    assert db.clocks["clk"].unc_setup == Fraction(3, 10)
    assert db.clocks["clk"].unc_hold == Fraction(3, 10)


def test_clock_latency_source_vs_network(query):
    db = parse_sdc(
        "create_clock -period 10 [get_ports clk]\n"
        "set_clock_latency -source -max 3 [get_clocks clk]\n"
        "set_clock_latency 1 [get_clocks clk]\n", query)
    clk = db.clocks["clk"]
    assert clk.latency_source_max == 3
    assert clk.latency_network_max == 1


def test_propagated_clock_clears_network_latency(query):
    db = parse_sdc(
        "create_clock -period 10 [get_ports clk]\n"
        "set_clock_latency 1 [get_clocks clk]\n"
        "set_propagated_clock [get_clocks clk]\n", query)
    clk = db.clocks["clk"]
    assert clk.propagated
    assert clk.latency_network_max == 0


# ---------------------------------------------------------------- I/O 约束


def test_input_delay(query):
    db = parse_sdc(
        "create_clock -period 20 [get_ports clk]\n"
        "set_input_delay -max 7.4 -clock clk [get_ports A]\n", query)
    spec = db.input_delays["A"][0]
    assert spec.value_max == Fraction(74, 10)
    assert spec.clock == "clk"


def test_output_delay_add_delay_keeps_both(query):
    """没有 -add_delay 的同类约束会互相覆盖；加了才会共存。"""
    db = parse_sdc(
        "create_clock -period 20 [get_ports clk]\n"
        "set_output_delay -max 2.5 -clock clk [get_ports B]\n"
        "set_output_delay -max 4.5 -clock clk -add_delay [get_ports B]\n", query)
    assert [s.value_max for s in db.output_delays["B"]] == [Fraction(5, 2), Fraction(9, 2)]


def test_output_delay_without_add_delay_overwrites(query):
    db = parse_sdc(
        "create_clock -period 20 [get_ports clk]\n"
        "set_output_delay -max 2.5 -clock clk [get_ports B]\n"
        "set_output_delay -max 4.5 -clock clk [get_ports B]\n", query)
    assert len(db.output_delays["B"]) == 1
    assert db.output_delays["B"][0].value_max == Fraction(9, 2)
    assert any("覆盖" in w for w in db.warnings)


# ------------------------------------------------------------------ 例外


def test_false_path_with_clocks(query):
    db = parse_sdc(
        "create_clock -period 20 [get_ports clk]\n"
        "create_clock -name clka -period 30\n"
        "set_false_path -from [get_clocks clka] -to [get_clocks clk]\n", query)
    exc = db.exceptions[0]
    assert exc.kind == "false_path"
    assert exc.filt.from_[0].kind == "clock"
    assert [r.name for r in exc.filt.to] == ["clk"]


def test_false_path_multiple_through(query):
    db = parse_sdc(
        "set_false_path -from [get_ports A] -through [get_pins MUX0/I0] "
        "-through [get_pins MUX1/I1] -to [get_ports B]\n", query)
    names = [r.name for r in db.exceptions[0].filt.through]
    assert names == ["MUX0/I0", "MUX1/I1"]


def test_get_pins_wildcard(query):
    db = parse_sdc("set_max_delay 5 -to [get_pins FF3[*]/D]\n", query)
    names = [r.name for r in db.exceptions[0].filt.to]
    assert names == ["FF3[0]/D", "FF3[1]/D"]


def test_bare_pin_name_without_get_pins(query):
    """SDC 里允许直接写 FF1/CP 这种对象名。"""
    db = parse_sdc(
        "set_multicycle_path -setup 2 -from FF1/CP -through MUX0/I0 -to FF2/D\n", query)
    mc = db.multicycles[0]
    assert mc.kind == "setup" and mc.cycles == 2
    assert mc.filt.from_[0].name == "FF1/CP"
    assert mc.filt.to[0].name == "FF2/D"


def test_multicycle_hold(query):
    db = parse_sdc("set_multicycle_path -hold 5 -to [get_pins FF3[*]/D]\n", query)
    assert db.multicycles[0].kind == "hold"
    assert db.multicycles[0].cycles == 5


def test_clock_groups(query):
    db = parse_sdc(
        "create_clock -period 20 [get_ports clk]\n"
        "create_clock -name clka -period 30\n"
        "set_clock_groups -asynchronous -group [get_clocks clka] -group [get_clocks clk]\n",
        query)
    cg = db.clock_groups[0]
    assert cg.asynchronous
    assert cg.groups == [["clka"], ["clk"]]


def test_unsupported_command_raises(query):
    with pytest.raises(SdcError):
        parse_sdc("set_magic_option -foo bar\n", query)


# ------------------------------------------------------ 真实约束文件能跑通


def test_all_example_sdc_parse():
    """每个示例的网表 + SDC 组合都要能完整跑通（get_ports/get_pins 需要设计对象）。"""
    from pysta.checks import EXAMPLES
    from pysta.timing import Design

    for ex in EXAMPLES:
        design = Design.build(
            paths.DEFAULT_LIB,
            paths.NETLIST_DIR / f"{ex.netlist_name}.v",
            paths.SDC_DIR / f"{ex.sdc_name}.sdc")
        assert design.sdc.clocks, f"{ex.name} 里没有解析出任何时钟"


def test_parse_without_query_reports_missing_source():
    """没有设计对象时，create_clock 找不到源端口应当报错而不是默默建个空时钟。"""
    with pytest.raises(SdcError):
        parse_sdc("create_clock -period 10 [get_ports clk]\n")
