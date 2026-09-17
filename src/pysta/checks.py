# -*- coding: utf-8 -*-
"""
示例的期望值表
==============

每个示例在给定工艺库 / 门级网表 / SDC 下，各条路径的时序预算与 slack
应该是多少，都在这里写成断言。两个地方会用到它：

* ``pysta check`` / ``pysta run`` 命令行（打印对照表）
* ``tests/test_examples.py``（pytest 回归测试）

这里所有期望值都能用 ``docs/`` 里的公式手算出来。一旦哪条对不上，
就说明引擎或者示例被改坏了，应该查清楚而不是改期望值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable

from .timing import AnalysisResult, Design, ZERO

__all__ = ["Check", "Example", "EXAMPLES", "budget_of", "pick"]

TOL = 1e-6


# --------------------------------------------------------------------------
# 选择器
# --------------------------------------------------------------------------


def pick(res: AnalysisResult, kind: str | None = None, start: str | None = None,
         end: str | None = None, index: int = 0):
    """按类别/起点/终点挑一条路径。"""
    hits = []
    for pt in res.paths:
        p = pt.path
        if kind and p.kind != kind:
            continue
        if start and p.startpoint.replace("@port:", "") != start:
            continue
        if end and p.endpoint.replace("@port:", "") != end:
            continue
        hits.append(pt)
    if not hits:
        raise KeyError(f"找不到路径 kind={kind} start={start} end={end}")
    return hits[index]


def budget_of(res: AnalysisResult, kind: str) -> float:
    """某类路径里最严格的时序预算（预算与走哪条布线无关，只跟时钟沿关系有关）。"""
    vals = [pt.budget_setup for pt in res.paths
            if pt.path.kind == kind and pt.budget_setup is not None]
    if not vals:
        raise KeyError(f"没有已约束的 {kind} 路径")
    return float(min(vals))


def _slack_of(res: AnalysisResult, kind: str) -> float:
    vals = [pt.slack_setup for pt in res.paths
            if pt.path.kind == kind and pt.slack_setup is not None]
    return float(min(vals))


def _disabled_count(res: AnalysisResult, kind: str, needle: str = "") -> int:
    return sum(1 for pt in res.paths
               if pt.path.kind == kind and pt.path.setup_disabled
               and needle in (pt.path.setup_disabled or ""))


def _constrained_count(res: AnalysisResult, kind: str) -> int:
    return sum(1 for pt in res.paths
               if pt.path.kind == kind and pt.slack_setup is not None)


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class Check:
    """一条对照项：某个可测量应当等于某个期望值。"""

    what: str                                   # 被检查的约束（写成公式形式）
    expected: float | int | str | bool          # 期望值
    value: Callable[[Design, AnalysisResult], object]
    tol: float = TOL
    unit: str = "ns"
    detail: str = ""

    def actual(self, design: Design, res: AnalysisResult) -> object:
        return self.value(design, res)

    def ok(self, design: Design, res: AnalysisResult) -> bool:
        got = self.actual(design, res)
        if isinstance(self.expected, float) and isinstance(got, (int, float, Fraction)):
            return abs(float(got) - self.expected) <= self.tol
        return got == self.expected

    def show(self) -> str:
        if isinstance(self.expected, float):
            return f"{self.expected:g}{self.unit}"
        if isinstance(self.expected, bool):
            return "是" if self.expected else "否"
        return str(self.expected)

    def show_actual(self, design: Design, res: AnalysisResult) -> str:
        got = self.actual(design, res)
        if isinstance(got, float):
            return f"{got:g}{self.unit}"
        if isinstance(got, bool):
            return "是" if got else "否"
        return str(got)


@dataclass
class Example:
    """一个示例：门级网表 + SDC + 它覆盖的时序概念。"""

    name: str                  # 网表/SDC 文件主名
    title: str
    topic: str                 # 覆盖的时序概念
    desc: str = ""
    expect: str = ""           # 卡片上显示的一句话结论
    sdc: str | None = None     # 默认同名
    netlist: str | None = None # 默认同名（ex9b 与 ex9 共用同一张网表）
    checks: list[Check] = field(default_factory=list)

    @property
    def sdc_name(self) -> str:
        return self.sdc or self.name

    @property
    def netlist_name(self) -> str:
        return self.netlist or self.name


def _c(what, expected, fn, **kw) -> Check:
    return Check(what=what, expected=expected, value=fn, **kw)


# --------------------------------------------------------------------------
# 各例的对照项
# --------------------------------------------------------------------------

EXAMPLES: list[Example] = [

    Example(
        name="ex0_clk_attr",
        title="时钟属性：不确定度 / 转换时间 / 延时",
        topic="时钟建模",
        desc="skew 与 jitter 统一用 set_clock_uncertainty 建模；转换时间用 "
             "set_clock_transition；延时拆成 source latency 与 network latency "
             "分开约束，布线后改用 set_propagated_clock。",
        expect="uncertainty 会直接从预算里扣掉；source latency 发送端与捕获端共有，"
               "会相互抵消，不影响 slack。",
        checks=[
            _c("budget = Tclk - Tco - Tsu - uncertainty",
               7.5, lambda d, r: budget_of(r, "reg->reg"),
               detail="10 - 1.0 - 1.0 - 0.5 = 7.5"),
        ],
    ),

    Example(
        name="ex1_reg2reg",
        title="寄存器 CP -> 寄存器 D",
        topic="寄存器到寄存器",
        desc="只要 create_clock 定义了时钟，寄存器之间的路径就自动被约束，"
             "不需要任何额外约束。",
        expect="TN <= Tclk - Tco - Tsu - Tuncertainty，Tclk=10ns 时预算为 8.0ns。",
        checks=[
            _c("TN <= Tclk - Tco - Tsu - Tuncertainty",
               8.0, lambda d, r: budget_of(r, "reg->reg"),
               detail="10 - 1.0(Tco) - 1.0(Tsu) - 0 = 8.0"),
        ],
    ),

    Example(
        name="ex2_in2reg",
        title="输入端口 -> 寄存器 D",
        topic="输入延时约束",
        desc="芯片外部已经占掉了一部分周期，必须用 set_input_delay 把这部分"
             "告诉综合 / 时序工具，否则内部逻辑的预算会被算错。",
        expect="内部逻辑最大延时 = 20 - 7.4 - 1 = 11.6ns",
        checks=[
            _c("budget = Tclk - input_delay - Tsu",
               11.6, lambda d, r: budget_of(r, "input->reg"),
               detail="20 - 7.4 - 1.0 = 11.6"),
        ],
    ),

    Example(
        name="ex3_reg2out",
        title="寄存器 CP -> 输出端口",
        topic="输出延时约束",
        desc="用 set_output_delay 把芯片外部那一段（组合逻辑 + 下游寄存器的"
             "建立时间）一起告诉工具。",
        expect="逻辑最大延时 = 20 - 5.4 - 1 - 1 = 12.6ns"
               "（output_delay 6.4 = 外部组合 5.4 + 下游 Tsu 1.0）",
        checks=[
            _c("budget = Tclk - Tco - output_delay",
               12.6, lambda d, r: budget_of(r, "reg->output"),
               detail="20 - 1.0 - 6.4 = 12.6"),
        ],
    ),

    Example(
        name="ex4_in2out",
        title="输入端口 -> 输出端口（纯组合）",
        topic="纯组合路径",
        desc="中间没有寄存器，靠两端的 input/output delay 把组合逻辑夹住；"
             "也可以直接用 set_max_delay 卡这条路径的延时。",
        expect="Tm = Tclk - input_delay - output_delay = 20 - 0.4 - 0.8 = 18.8ns",
        checks=[
            _c("budget = Tclk - input_delay - output_delay",
               18.8, lambda d, r: budget_of(r, "input->output"),
               detail="20 - 0.4 - 0.8 = 18.8"),
        ],
    ),

    Example(
        name="ex5_multi_clk_in",
        title="多时钟：输入端口",
        topic="虚拟时钟与公共基本周期",
        desc="发送时钟用虚拟时钟定义。公共基本周期 = LCM(30,20) = 60ns，"
             "工具要遍历 0ns 和 30ns 两个发送沿，取最严格的那个。",
        expect="发送沿 0ns -> 捕获沿 20ns：预算 20-5.5-Tsu；"
               "发送沿 30ns -> 捕获沿 40ns：预算 10-5.5-Tsu。取最严格 ⇒ 3.5ns",
        checks=[
            _c("最严格 budget = 10 - 5.5 - Tsu",
               3.5, lambda d, r: budget_of(r, "input->reg"),
               detail="10 - 5.5 - 1.0 = 3.5"),
            _c("最差发送沿落在第二个沿 30ns，而不是 0ns",
               30.0, lambda d, r: float(pick(r, "input->reg").launch_edge)),
            _c("对应的捕获沿落在 40ns",
               40.0, lambda d, r: float(pick(r, "input->reg").capture_edge_setup)),
        ],
    ),

    Example(
        name="ex6_multi_clk_out",
        title="多时钟：输出端口与 -add_delay",
        topic="多捕获时钟取最差",
        desc="clkc 的周期写成 [expr 1.0/75*1000]（精确值 40/3 ns）。同一个端口上"
             "挂两条 set_output_delay，第二条必须加 -add_delay，"
             "否则会把第一条覆盖掉。每条约束要分别算再取最严格。",
        expect="对 clkd 是 10-4.5-Tco，对 clkc 是 20/3-2.5-Tco，"
               "取最严格 ⇒ 预算 3.1667ns",
        checks=[
            _c("最严格 budget = 20/3 - 2.5 - Tco",
               20.0 / 3 - 2.5 - 1.0, lambda d, r: budget_of(r, "reg->output"),
               detail="6.6667 - 2.5 - 1.0 = 3.1667"),
            _c("clkc 周期 = 1.0/75*1000 = 40/3 ns（精确值）",
               40.0 / 3, lambda d, r: float(d.sdc.clocks["clkc"].period)),
            _c("最差发送沿是 20ns（不是 0ns）",
               20.0, lambda d, r: float(pick(r, "reg->output").launch_edge)),
            _c("最差捕获沿 26.667ns（即精确的 80/3 ns）",
               20.0 / 3 + 20.0,
               lambda d, r: float(pick(r, "reg->output").capture_edge_setup)),
            _c("端口 B 的两条 output_delay 都保留了（-add_delay 生效）",
               2, lambda d, r: len(d.sdc.output_delays["B"])),
        ],
    ),

    Example(
        name="ex7_async_cdc",
        title="时序例外：异步路径",
        topic="set_false_path",
        desc="两个时钟来自独立时钟源，相位关系不确定，跨时钟域路径用 "
             "set_false_path 解除约束 —— 相位不确定的路径做时序分析没有意义。",
        expect="两个方向的共 2 条跨时钟域路径都被屏蔽。",
        checks=[
            _c("被 set_false_path 屏蔽的跨时钟域路径条数",
               2, lambda d, r: _disabled_count(r, "reg->reg", "set_false_path")),
            _c("没有剩下未被屏蔽的 reg->reg 路径",
               0, lambda d, r: _constrained_count(r, "reg->reg")),
            _c("时钟周期：clka = 20ns（50MHz）",
               20.0, lambda d, r: float(d.sdc.clocks["clka"].period)),
            _c("时钟周期：clkb = 10ns（100MHz）",
               10.0, lambda d, r: float(d.sdc.clocks["clkb"].period)),
        ],
    ),

    Example(
        name="ex8_pseudo_path",
        title="时序例外：逻辑伪路径",
        topic="-through 匹配",
        desc="两个共用选择信号的 MUX 串接，交叉通路物理上连着、逻辑上不通。"
             "set_false_path 的多个 -through 之间是「且」的关系。",
        expect="A->B 的 4 条路径里，2 条交叉伪路径被屏蔽，2 条真实通路正常分析。",
        checks=[
            _c("被屏蔽的交叉伪路径条数",
               2, lambda d, r: _disabled_count(r, "input->output", "set_false_path")),
            _c("仍参与时序检查的真实通路条数",
               2, lambda d, r: _constrained_count(r, "input->output")),
            _c("真实通路 budget = Tclk - input_delay - output_delay",
               5.0, lambda d, r: budget_of(r, "input->output")),
        ],
    ),

    Example(
        name="ex9_multicycle_adder",
        title="多周期路径（正确约束）",
        topic="多周期：setup 与 hold 配对",
        desc="set_multicycle_path -setup 6 把建立检查推到第 6 个上升沿（60ns），"
             "但会把保持检查连带挪到 50ns；必须再补 -hold 5 把它拉回 0ns。",
        expect="建立 budget = 60 - Tco - Tsu = 58ns；保持检查沿被拉回 0ns。",
        checks=[
            _c("budget = 6*Tclk - Tco - Tsu",
               58.0, lambda d, r: budget_of(r, "reg->reg"),
               detail="60 - 1.0 - 1.0 = 58.0"),
            _c("建立捕获沿落在第 6 个上升沿 60ns",
               60.0, lambda d, r: float(pick(r, "reg->reg").capture_edge_setup)),
            _c("保持检查沿被 -hold 5 拉回 0ns",
               0.0, lambda d, r: float(pick(r, "reg->reg").capture_edge_hold)),
            _c("多周期数 setup/hold",
               "6/5",
               lambda d, r: f"{pick(r, 'reg->reg').m_setup}/{pick(r, 'reg->reg').m_hold}"),
            _c("保持裕量为正（约束合理）",
               True, lambda d, r: min(p.slack_hold for p in r.by_kind("reg->reg")
                                      if p.slack_hold is not None) > 0),
        ],
    ),

    Example(
        name="ex9b_multicycle_adder_default_hold",
        title="多周期路径的反例：只写 -setup 6",
        topic="多周期：漏掉 -hold 的后果",
        desc="故意不加 -hold 5。保持检查会停在 50ns，要求组合逻辑延时 >= 50.5ns，"
             "而建立又要求 <= 58ns —— 工具会被迫去凑一条毫无必要的长延时路径。",
        expect="保持检查沿停在 50ns ⇒ 产生 -49ns 量级的、毫无意义的保持违例。",
        sdc="ex9b_multicycle_adder_default_hold",
        netlist="ex9_multicycle_adder",
        checks=[
            _c("保持检查沿停在 50ns（建立沿的前一个沿）",
               50.0, lambda d, r: float(pick(r, "reg->reg").capture_edge_hold)),
            _c("保持违例严重（证明这条约束是错的）",
               True, lambda d, r: min(p.slack_hold for p in r.by_kind("reg->reg")
                                      if p.slack_hold is not None) < -40.0),
            _c("建立 budget 本身没问题，仍是 58ns",
               58.0, lambda d, r: budget_of(r, "reg->reg")),
        ],
    ),

    Example(
        name="ex10_multicycle_mixed",
        title="多周期与单周期路径共存",
        topic="-through 限定作用范围",
        desc="乘法路径要 2 个周期、异或路径只要 1 个周期。-through Multiply/out "
             "把多周期约束**只**加在乘法那条路径上，另一条保持默认。",
        expect="乘法路径 budget = 20 - Tco - Tsu = 17.9ns（Tco 因扇出为 2 变成 1.1ns）；"
               "异或路径仍按 1 个周期，budget 7.9ns。",
        checks=[
            _c("乘法路径被识别为 setup=2 / hold=1",
               "2/1",
               lambda d, r: f"{pick(r, 'reg->reg', end='FF2/D').m_setup}"
                            f"/{pick(r, 'reg->reg', end='FF2/D').m_hold}"),
            _c("乘法路径 budget = 20 - Tco - Tsu",
               17.9, lambda d, r: float(pick(r, "reg->reg", end="FF2/D").budget_setup),
               detail="Tco = 1.1ns（FF1/Q 扇出为 2）⇒ 20 - 1.1 - 1.0 = 17.9"),
            _c("异或路径仍是默认的 setup=1 / hold=0",
               "1/0",
               lambda d, r: f"{pick(r, 'reg->reg', end='FF3/D').m_setup}"
                            f"/{pick(r, 'reg->reg', end='FF3/D').m_hold}"),
            _c("异或路径 budget = 10 - Tco - Tsu",
               7.9, lambda d, r: float(pick(r, "reg->reg", end="FF3/D").budget_setup)),
        ],
    ),
]


def all_checks() -> list[tuple[str, Check]]:
    return [(ex.name, c) for ex in EXAMPLES for c in ex.checks]
