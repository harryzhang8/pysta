# -*- coding: utf-8 -*-
"""时序引擎测试：时钟沿关系、预算公式、路径分类、时序例外。"""

from __future__ import annotations

from fractions import Fraction

import pytest

from pysta import paths
from pysta.checks import EXAMPLES, budget_of, pick
from pysta.timing import (
    ClockEdges,
    Design,
    analyze,
    first_rise_index_after,
    lcm_frac,
)


# ------------------------------------------------------------- 公共基本周期


@pytest.mark.parametrize("a, b, want", [
    (10, 10, 10),
    (30, 20, 60),
    (20, 10, 20),
    (Fraction(20), Fraction(40, 3), 40),        # 20ns 与 75MHz
    (Fraction(40, 3), Fraction(10), 40),
    (Fraction(30), Fraction(20), 60),
])
def test_lcm_frac(a, b, want):
    assert lcm_frac(Fraction(a), Fraction(b)) == Fraction(want)


def test_lcm_frac_is_exact_not_float():
    """40/3 ns 这种周期必须是精确值，否则 13.3333*3 会得到 39.9999。"""
    base = lcm_frac(Fraction(20), Fraction(40, 3))
    assert base == 40
    assert base / Fraction(40, 3) == 3          # 正好 3 个周期


# ------------------------------------------------------------- 时钟沿序号


class _FakeClock:
    """只带 rise_time / period 的最小对象，用来单测沿序号计算。"""

    def __init__(self, rise: Fraction, period: Fraction):
        self._rise = rise
        self.period = period

    def rise_time(self) -> Fraction:
        return self._rise


def edges(rise=0, period=10) -> ClockEdges:
    return ClockEdges(_FakeClock(Fraction(rise), Fraction(period)))


def test_first_rise_index_after():
    e = edges()
    assert first_rise_index_after(e, Fraction(0)) == 1      # 0ns 之后第一个沿是 10ns
    assert first_rise_index_after(e, Fraction(-1)) == 0     # -1ns 之后第一个沿是 0ns
    assert first_rise_index_after(e, Fraction(19)) == 2     # 19ns 之后第一个沿是 20ns


def test_first_rise_index_after_multi_clock():
    launch, capture = edges(0, 30), edges(0, 20)
    assert first_rise_index_after(capture, launch.rise(0)) == 1     # 0 -> 20
    assert first_rise_index_after(capture, launch.rise(1)) == 2     # 30 -> 40


def test_three_phase_clock_edges():
    clkc = edges(0, Fraction(40, 3))
    assert clkc.rise(1) == Fraction(40, 3)
    assert clkc.rise(2) == Fraction(80, 3)
    assert clkc.rise(3) == 40


# ------------------------------------------------------------------- 构建


def build(tmp_path, netlist: str, sdc: str) -> Design:
    n = tmp_path / "t.v"
    s = tmp_path / "t.sdc"
    n.write_text(netlist, encoding="utf-8")
    s.write_text(sdc, encoding="utf-8")
    return Design.build(paths.DEFAULT_LIB, n, s)


REG2REG = """
module t (clk, din, dout);
  input  clk;
  input  din;
  output dout;
  wire q1, n1;
  DFFRQ FF1 (.CP(clk), .D(din), .Q(q1));
  INVX1 U1  (.A(q1),  .Y(n1));
  DFFRQ FF2 (.CP(clk), .D(n1),  .Q(dout));
endmodule
"""


def test_reg2reg_budget(tmp_path):
    """紧凑的寄存器间路径：budget = Tclk - Tco - Tsu。"""
    d = build(tmp_path, REG2REG, "create_clock -period 10 [get_ports clk]\n")
    res = analyze(d)
    assert budget_of(res, "reg->reg") == pytest.approx(8.0)   # 10 - 1.0 - 1.0
    assert pick(res, "reg->reg").comb_delay == Fraction(3, 50)   # 只有一级 INVX1


def test_slack_equals_budget_minus_delay(tmp_path):
    d = build(tmp_path, REG2REG, "create_clock -period 10 [get_ports clk]\n")
    pt = pick(analyze(d), "reg->reg")
    assert pt.slack_setup == pt.budget_setup - pt.comb_delay


def test_tight_clock_creates_violation(tmp_path):
    d = build(tmp_path, REG2REG, "create_clock -period 2 [get_ports clk]\n")
    pt = pick(analyze(d), "reg->reg")
    assert pt.slack_setup < 0
    assert pt.verdict == "VIOLATED"


def test_uncertainty_reduces_budget(tmp_path):
    sdc = ("create_clock -period 10 [get_ports clk]\n"
           "set_clock_uncertainty -setup 0.5 [get_clocks clk]\n")
    d = build(tmp_path, REG2REG, sdc)
    assert budget_of(analyze(d), "reg->reg") == pytest.approx(7.5)


def test_source_latency_cancels_out(tmp_path):
    """source latency 发送端和捕获端共有，不影响 slack。"""
    base = build(tmp_path, REG2REG, "create_clock -period 10 [get_ports clk]\n")
    with_lat = build(tmp_path, REG2REG,
                     "create_clock -period 10 [get_ports clk]\n"
                     "set_clock_latency -source -max 3 [get_clocks clk]\n")
    a = pick(analyze(base), "reg->reg").slack_setup
    b = pick(analyze(with_lat), "reg->reg").slack_setup
    assert a == b


IN2REG = """
module t (clk, A, Q);
  input  clk;
  input  A;
  output Q;
  wire n1;
  INVX1 U1 (.A(A), .Y(n1));
  DFFRQ FF (.CP(clk), .D(n1), .Q(Q));
endmodule
"""


def test_input_delay_budget(tmp_path):
    sdc = ("create_clock -period 20 [get_ports clk]\n"
           "set_input_delay -max 7.4 -clock clk [get_ports A]\n")
    d = build(tmp_path, IN2REG, sdc)
    assert budget_of(analyze(d), "input->reg") == pytest.approx(11.6)


def test_input_without_delay_is_unconstrained(tmp_path):
    """没有 set_input_delay 的输入端口，不应报出假的保持违例。"""
    d = build(tmp_path, IN2REG, "create_clock -period 20 [get_ports clk]\n")
    pts = [p for p in analyze(d).paths if p.path.kind == "input->reg"]
    assert pts and all(p.slack_setup is None for p in pts)
    assert all("set_input_delay" in p.note for p in pts)


IN2OUT = """
module t (clk, C, D);
  input  clk;
  input  C;
  output D;
  wire n1;
  INVX1 U1 (.A(C), .Y(n1));
  BUFX1 U2 (.A(n1), .Y(D));
endmodule
"""


def test_in2out_budget(tmp_path):
    sdc = ("create_clock -period 20 [get_ports clk]\n"
           "set_input_delay  0.4 -clock clk [get_ports C]\n"
           "set_output_delay 0.8 -clock clk [get_ports D]\n")
    d = build(tmp_path, IN2OUT, sdc)
    assert budget_of(analyze(d), "input->output") == pytest.approx(18.8)


def test_max_delay_overrides_io_delays(tmp_path):
    """set_max_delay 直接卡死端口到端口的延时。"""
    sdc = ("create_clock -period 20 [get_ports clk]\n"
           "set_input_delay  0.4 -clock clk [get_ports C]\n"
           "set_output_delay 0.8 -clock clk [get_ports D]\n"
           "set_max_delay 1.2 -from [get_ports C] -to [get_ports D]\n")
    d = build(tmp_path, IN2OUT, sdc)
    pt = pick(analyze(d), "input->output")
    # 取更严格的那个：min(18.8, 1.2) => 1.2
    assert pt.budget_setup == pytest.approx(1.2)


# --------------------------------------------------------------- 多周期语义


def _ex(name: str):
    ex = next(e for e in EXAMPLES if e.name == name)
    d = Design.build(paths.DEFAULT_LIB,
                     paths.NETLIST_DIR / f"{ex.netlist_name}.v",
                     paths.SDC_DIR / f"{ex.sdc_name}.sdc")
    return d, analyze(d)


def test_multicycle_setup_pushes_capture_edge():
    _, res = _ex("ex9_multicycle_adder")
    pt = pick(res, "reg->reg")
    assert (pt.m_setup, pt.m_hold) == (6, 5)
    assert pt.capture_edge_setup == 60          # 第 6 个上升沿


def test_multicycle_hold_flag_pulls_edge_back():
    """写了 -setup 就必须补 -hold，否则保持沿会停在 50ns。"""
    _, ok = _ex("ex9_multicycle_adder")
    _, bad = _ex("ex9b_multicycle_adder_default_hold")
    assert pick(ok, "reg->reg").capture_edge_hold == 0
    assert pick(bad, "reg->reg").capture_edge_hold == 50


def test_missing_hold_flag_causes_absurd_violation():
    _, res = _ex("ex9b_multicycle_adder_default_hold")
    worst = min(p.slack_hold for p in res.by_kind("reg->reg"))
    assert worst < -40


def test_multicycle_through_limits_scope():
    """-through 只作用于乘法路径，异或路径保持默认 1 周期。"""
    _, res = _ex("ex10_multicycle_mixed")
    assert pick(res, "reg->reg", end="FF2/D").m_setup == 2
    assert pick(res, "reg->reg", end="FF3/D").m_setup == 1


# ------------------------------------------------------------- 时序例外


def test_false_path_is_direction_aware():
    """-from/-to 有方向：clka->clkb 不能把 clkb->clka 也一起命中。"""
    _, res = _ex("ex7_async_cdc")
    reasons = {p.path.startpoint: p.path.setup_disabled for p in res.by_kind("reg->reg")}
    assert "clka" in reasons["FF_A1/CP"] and "clkb" in reasons["FF_A1/CP"]
    assert "clkb" in reasons["FF_B1/CP"] and "clka" in reasons["FF_B1/CP"]


def test_false_path_excludes_from_analysis():
    _, res = _ex("ex7_async_cdc")
    assert all(p.slack_setup is None for p in res.by_kind("reg->reg"))


def test_through_requires_all_points():
    """多个 -through 是"且"的关系，只命中一个不算。"""
    _, res = _ex("ex8_pseudo_path")
    disabled = [p for p in res.by_kind("input->output") if p.path.setup_disabled]
    kept = [p for p in res.by_kind("input->output") if p.slack_setup is not None]
    assert len(disabled) == 2 and len(kept) == 2


# --------------------------------------------------------- 多约束组合取最差


def test_multiple_output_delays_take_worst():
    """同一端口挂多条约束时，取最严格的那条，而不是数值最大的那条。"""
    _, res = _ex("ex6_multi_clk_out")
    assert budget_of(res, "reg->output") == pytest.approx(20.0 / 3 - 2.5 - 1.0)
