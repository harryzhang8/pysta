# -*- coding: utf-8 -*-
"""
Liberty (.lib) 子集解析器
=========================

为什么要自己写一个？因为本项目的目标是**不依赖商业 EDA 工具**也能完整跑通
"RTL -> 网表 -> SDC 约束 -> 静态时序分析(STA)" 这条链路，而链路里
工艺库的输入格式就是 Liberty。

本实现只覆盖时序分析真正需要的那部分语法：

    library (name) {
      time_unit : "1ns";
      cell (DFFRQ) {
        ff (IQ, IQN) { next_state : "D"; clocked_on : "CK"; }
        pin (CK) { direction : input; capacitance : 0.01; }
        pin (Q)  { direction : output; function : "IQ";
                   timing() { related_pin : "CK"; timing_type : rising_edge;
                              cell_rise (tbl) { ... } } }
        pin (D)  { direction : input;
                   timing() { related_pin : "CK"; timing_type : setup_rising;
                              rise_constraint (tbl) { ... } } }
      }
    }

数值一律用 ``fractions.Fraction`` 保存，保证 40/3 ns 这类周期不会因为
浮点误差在"公共基本周期"计算里被算错（例如 75MHz 对应的 40/3 ns）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

__all__ = [
    "LibertyParseError",
    "Table",
    "Pin",
    "CellArc",
    "Cell",
    "Library",
    "parse_liberty",
]


class LibertyParseError(Exception):
    """Liberty 语法/语义错误。"""


# --------------------------------------------------------------------------
# 词法分析
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<comment>/\*.*?\*/|//[^\n]*)
    | (?P<string>"(?:[^"\\]|\\.)*")
    | (?P<punct>[{}();:,])
    | (?P<number>[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
    | (?P<ident>[^\s{}();:,"]+)
    """,
    re.VERBOSE | re.DOTALL,
)


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN_RE.match(text, pos)
        if m is None:
            raise LibertyParseError(f"无法识别的字符 @{pos}: {text[pos:pos + 20]!r}")
        pos = m.end()
        if m.lastgroup in ("ws", "comment"):
            continue
        tokens.append(m.group())
    return tokens


def _to_number(raw: str) -> Fraction:
    """把 Liberty 的字面量转成精确有理数。"""
    s = raw.strip().strip('"')
    m = re.match(r"^([+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)", s)
    if not m:
        raise LibertyParseError(f"不是数字: {raw!r}")
    return Fraction(m.group(1))


def _unquote(raw: str | None) -> str | None:
    """去掉 Liberty 字符串字面量两边的双引号。"""
    if raw is None:
        return None
    return raw.strip().strip('"')


# --------------------------------------------------------------------------
# 通用语法树
# --------------------------------------------------------------------------


@dataclass
class Group:
    """Liberty 里 ``name (args) { body }`` 这样的一个 group。"""

    name: str
    args: list[str] = field(default_factory=list)
    attrs: dict[str, str] = field(default_factory=dict)
    groups: list["Group"] = field(default_factory=list)

    def find_all(self, name: str) -> list["Group"]:
        return [g for g in self.groups if g.name == name]

    def find(self, name: str) -> "Group | None":
        for g in self.groups:
            if g.name == name:
                return g
        return None

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Group {self.name} args={self.args} attrs={list(self.attrs)}>"


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.toks = tokens
        self.i = 0

    def peek(self) -> str | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self) -> str:
        if self.i >= len(self.toks):
            raise LibertyParseError("文件意外结束")
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, tok: str) -> None:
        got = self.next()
        if got != tok:
            raise LibertyParseError(f"期望 {tok!r}，实际 {got!r}")

    # -- body ------------------------------------------------------------
    def parse_body(self, stop: str | None) -> tuple[dict[str, str], list[Group]]:
        attrs: dict[str, str] = {}
        groups: list[Group] = []
        while True:
            t = self.peek()
            if t is None:
                if stop is not None:
                    raise LibertyParseError(f"缺少闭合的 {stop!r}")
                return attrs, groups
            if t == stop:
                self.next()
                return attrs, groups
            if t == "}":
                raise LibertyParseError("多余的 '}'")

            name = self.next()
            nxt = self.peek()
            if nxt == ":":
                self.next()
                value = self.next()
                # 少数属性值可以是没有引号的标识符，这里已经覆盖
                if self.peek() == ";":
                    self.next()
                attrs[name] = value
            elif nxt == "(":
                args = self.parse_args()
                if self.peek() == ";":
                    # Liberty 的"复合属性"，例如 capacitive_load_unit (1, pf);
                    self.next()
                    attrs[name] = ",".join(args)
                    continue
                self.expect("{")
                sub_attrs, sub_groups = self.parse_body("}")
                groups.append(Group(name, args, sub_attrs, sub_groups))
            elif nxt == ";":
                self.next()
                attrs[name] = "true"
            elif nxt == "{":
                # 罕见：无参数 group
                self.next()
                sub_attrs, sub_groups = self.parse_body("}")
                groups.append(Group(name, [], sub_attrs, sub_groups))
            else:
                raise LibertyParseError(f"group {name!r} 后面的语法不认识: {nxt!r}")

    def parse_args(self) -> list[str]:
        self.expect("(")
        args: list[str] = []
        cur: list[str] = []
        depth = 0
        while True:
            t = self.next()
            if t == "(":
                depth += 1
                cur.append(t)
            elif t == ")":
                if depth == 0:
                    if cur:
                        args.append(" ".join(cur))
                    return args
                depth -= 1
                cur.append(t)
            elif t == "," and depth == 0:
                args.append(" ".join(cur))
                cur = []
            else:
                cur.append(t)


# --------------------------------------------------------------------------
# 时序模型
# --------------------------------------------------------------------------


@dataclass
class Table:
    """NLDM 二维查表：index1 = 输入转换时间, index2 = 输出负载。

    只给一个点时退化成常数；给两个点时可以线性插值/外推。
    这样既保留了真实 .lib 的负载相关性（Tco 会随扇出变化），
    又能让示例里的手算数字保持整洁。
    """

    index1: list[Fraction]
    index2: list[Fraction]
    values: list[list[Fraction]]

    @staticmethod
    def constant(v: Fraction | int | float | str) -> "Table":
        val = v if isinstance(v, Fraction) else Fraction(str(v))
        return Table([Fraction(0)], [Fraction(0)], [[val]])

    def lookup(self, i1: Fraction | float, i2: Fraction | float) -> Fraction:
        x = i1 if isinstance(i1, Fraction) else Fraction(str(i1))
        y = i2 if isinstance(i2, Fraction) else Fraction(str(i2))
        c1 = _interp_axis(self.index1, x)
        c2 = _interp_axis(self.index2, y)
        # 双线性
        v00 = self.values[c1[0]][c2[0]]
        v01 = self.values[c1[0]][c2[1]]
        v10 = self.values[c1[1]][c2[0]]
        v11 = self.values[c1[1]][c2[1]]
        bottom = v00 + (v01 - v00) * c2[2]
        top = v10 + (v11 - v10) * c2[2]
        return bottom + (top - bottom) * c1[2]


def _interp_axis(axis: list[Fraction], x: Fraction) -> tuple[int, int, Fraction]:
    """返回 (低索引, 高索引, 权重)。边界外做线性外推。"""
    if len(axis) == 1:
        return 0, 0, Fraction(0)
    if x <= axis[0]:
        lo, hi = 0, 1
    elif x >= axis[-1]:
        lo, hi = len(axis) - 2, len(axis) - 1
    else:
        lo = 0
        for i in range(len(axis) - 1):
            if axis[i] <= x <= axis[i + 1]:
                lo = i
                break
        hi = lo + 1
    span = axis[hi] - axis[lo]
    w = Fraction(0) if span == 0 else (x - axis[lo]) / span
    return lo, hi, w


@dataclass
class Pin:
    name: str
    direction: str = "input"          # input / output / inout / internal
    capacitance: Fraction = Fraction(0)
    function: str | None = None
    is_clock: bool = False
    three_state: str | None = None


@dataclass
class CellArc:
    """组合逻辑的一条 timing arc：related_pin -> 本 cell 的某个输出引脚。"""

    output_pin: str
    related_pin: str
    timing_type: str
    delay_rise: Table
    delay_fall: Table

    def delay(self, slew: Fraction | float, load: Fraction | float) -> Fraction:
        # 时序分析取最坏：上升/下降里较大的那个
        return max(self.delay_rise.lookup(slew, load), self.delay_fall.lookup(slew, load))


@dataclass
class Cell:
    name: str
    area: Fraction = Fraction(0)
    pins: dict[str, Pin] = field(default_factory=dict)
    arcs: list[CellArc] = field(default_factory=list)

    # 时序单元（触发器/锁存器）
    is_sequential: bool = False
    clock_pin: str | None = None
    next_state_pin: str | None = None
    output_pins: list[str] = field(default_factory=list)
    setup: Fraction = Fraction(0)
    hold: Fraction = Fraction(0)
    clk_to_q: Table = field(default_factory=lambda: Table.constant(0))

    def comb_arc_from(self, in_pin: str) -> CellArc | None:
        for a in self.arcs:
            if a.related_pin == in_pin:
                return a
        return None


@dataclass
class Library:
    name: str
    time_unit: str = "1ns"
    capacitive_load_unit: str = "1pf"
    cells: dict[str, Cell] = field(default_factory=dict)

    def cell(self, name: str) -> Cell:
        try:
            return self.cells[name]
        except KeyError:
            raise LibertyParseError(f"工艺库里没有单元 {name!r}") from None


# --------------------------------------------------------------------------
# 从语法树到 Library
# --------------------------------------------------------------------------


def _parse_table(g: Group | None, default: Fraction = Fraction(0)) -> Table:
    if g is None:
        return Table.constant(default)

    def axis_of(name: str) -> list[Fraction]:
        raw = g.attrs.get(name)
        if raw is None:
            return [Fraction(0)]
        return [_to_number(t) for t in raw.strip('"').split(",") if t.strip()]

    idx1 = axis_of("index_1")
    idx2 = axis_of("index_2")

    raw_values = g.attrs.get("values")
    if raw_values is None:
        return Table.constant(default)

    # 形如 "0.30, 0.55" 或 "0.30, 0.55, 0.38, 0.60"（按行展开）
    nums = [_to_number(t) for t in raw_values.strip('"').split(",") if t.strip()]
    rows: list[list[Fraction]] = []
    n2 = len(idx2)
    for r in range(len(idx1)):
        chunk = nums[r * n2:(r + 1) * n2]
        rows.append(chunk if chunk else [nums[0]])
    if not rows:
        rows = [[default]]
    if not idx1:
        idx1 = [Fraction(0)] * len(rows)
    if not idx2:
        idx2 = [Fraction(0)] * len(rows[0])
    return Table(idx1, idx2, rows)


def _parse_cell(g: Group) -> Cell:
    name = g.args[0] if g.args else g.attrs.get("name", "?")
    cell = Cell(name=name, area=_to_number(g.attrs.get("area", "0")))

    for pg in g.find_all("pin"):
        pin_name = pg.args[0] if pg.args else "?"
        pin = Pin(
            name=pin_name,
            direction=pg.attrs.get("direction", "input"),
            capacitance=_to_number(pg.attrs.get("capacitance", "0")),
            function=_unquote(pg.attrs.get("function")),
            is_clock=pg.attrs.get("clock", "").lower() in ("true", "1"),
            three_state=_unquote(pg.attrs.get("three_state")),
        )
        if pin.function is None and pg.attrs.get("clock", "").lower() in ("true", "1"):
            pin.function = pin_name
        cell.pins[pin_name] = pin

        for tg in pg.find_all("timing"):
            rel = (tg.attrs.get("related_pin") or "").strip('"')
            ttype = (tg.attrs.get("timing_type") or "combinational").strip('"')
            cell.arcs.append(
                CellArc(
                    output_pin=pin_name,
                    related_pin=rel,
                    timing_type=ttype,
                    delay_rise=_parse_table(tg.find("cell_rise")),
                    delay_fall=_parse_table(tg.find("cell_fall")),
                )
            )

    ffg = g.find("ff") or g.find("latch")
    if ffg is not None:
        cell.is_sequential = True
        next_state = (ffg.attrs.get("next_state") or "").strip('"').strip()
        clocked_on = (ffg.attrs.get("clocked_on") or "").strip('"').strip()
        cell.next_state_pin = next_state or None
        cell.clock_pin = clocked_on or None
        for p in cell.pins.values():
            if p.direction == "output":
                cell.output_pins.append(p.name)

        # 时钟 -> Q 的输出延迟
        for pg in g.find_all("pin"):
            pname = pg.args[0] if pg.args else "?"
            pin = cell.pins.get(pname)
            if pin is None or pin.direction != "output":
                continue
            for tg in pg.find_all("timing"):
                ttype = (tg.attrs.get("timing_type") or "").strip('"')
                if ttype in ("rising_edge", "falling_edge", "preset", "clear"):
                    cell.clk_to_q = _parse_table(tg.find("cell_rise"))
                    break

        # setup / hold 约束
        for pg in g.find_all("pin"):
            pname = pg.args[0] if pg.args else "?"
            for tg in pg.find_all("timing"):
                ttype = (tg.attrs.get("timing_type") or "").strip('"')
                if ttype.startswith("setup"):
                    cell.setup = _parse_table(tg.find("rise_constraint")).lookup(0, 0)
                elif ttype.startswith("hold"):
                    cell.hold = _parse_table(tg.find("rise_constraint")).lookup(0, 0)

    return cell


def parse_liberty(text: str) -> Library:
    """解析 Liberty 文本，返回 :class:`Library`。"""
    toks = _tokenize(text)
    p = _Parser(toks)
    attrs, groups = p.parse_body(None)

    lib_group: Group | None = None
    for g in groups:
        if g.name == "library":
            lib_group = g
            break
    if lib_group is None:
        raise LibertyParseError("没有找到 library(...) 顶层 group")

    lib = Library(
        name=lib_group.args[0] if lib_group.args else "unnamed",
        time_unit=lib_group.attrs.get("time_unit", '"1ns"').strip('"'),
        capacitive_load_unit=(lib_group.attrs.get("capacitive_load_unit") or "1,pf").strip('"'),
    )
    for cg in lib_group.find_all("cell"):
        cell = _parse_cell(cg)
        lib.cells[cell.name] = cell
    return lib


def parse_liberty_file(path) -> Library:
    from pathlib import Path

    return parse_liberty(Path(path).read_text(encoding="utf-8"))
