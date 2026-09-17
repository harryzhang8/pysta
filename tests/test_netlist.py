# -*- coding: utf-8 -*-
"""网表解析器测试。"""

from __future__ import annotations

import pytest

from pysta import paths
from pysta.netlist import NetlistError, parse_verilog, parse_verilog_file


def test_ports_and_instances():
    m = parse_verilog_file(paths.NETLIST_DIR / "ex1_reg2reg.v")
    assert m.name == "ex1_reg2reg"
    assert m.ports == {"clk": "input", "dina": "input", "douta": "output"}
    assert [i.name for i in m.instances] == ["FF1", "U1", "U2", "FF2"]
    assert m.instances[0].cell == "DFFRQ"
    assert m.instances[0].conns["CP"] == "clk"
    assert m.instances[0].conns["Q"] == "q1"


def test_array_instance_names():
    """`FF3[0]` 这种数组例化名要能解析（SDC 里用 get_pins FF3[*]/D 匹配）。"""
    m = parse_verilog_file(paths.NETLIST_DIR / "ex9_multicycle_adder.v")
    names = [i.name for i in m.instances]
    assert "FF3[0]" in names and "FF3[1]" in names
    ff30 = next(i for i in m.instances if i.name == "FF3[0]")
    assert ff30.conns["D"] == "sum0"


def test_positional_connections():
    m = parse_verilog(
        "module t (a, y);\n"
        "  input a; output y;\n"
        "  INVX1 u1 (a, y);\n"
        "endmodule\n")
    assert m.instances[0].conns == {"__pos0": "a", "__pos1": "y"}


def test_bus_declaration_expands():
    m = parse_verilog(
        "module t (A, Y);\n"
        "  input [1:0] A;\n"
        "  output Y;\n"
        "  wire [1:0] n;\n"
        "  INVX1 u1 (.A(A[0]), .Y(n[0]));\n"
        "endmodule\n")
    assert "A[0]" in m.ports and "A[1]" in m.ports
    assert m.instances[0].conns["A"] == "A[0]"


def test_assign_creates_alias():
    m = parse_verilog(
        "module t (a, y);\n"
        "  input a; output y; wire mid;\n"
        "  assign mid = a;\n"
        "  assign y = mid;\n"
        "endmodule\n")
    assert m.resolve("y") == "a"


def test_behavioral_code_is_rejected():
    """RTL 不能被当成网表 —— 必须给出明确的错误提示。"""
    with pytest.raises(NetlistError):
        parse_verilog(
            "module t (clk, d, q);\n"
            "  input clk, d; output reg q;\n"
            "  always @(posedge clk) q <= d;\n"
            "endmodule\n")


def test_all_example_netlists_parse():
    for v in sorted(paths.NETLIST_DIR.glob("*.v")):
        m = parse_verilog_file(v)
        assert m.instances, f"{v.name} 没有解析出任何例化"
