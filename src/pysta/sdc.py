# -*- coding: utf-8 -*-
"""
SDC / Tcl 约束解析器
====================

SDC = Synopsys Design Constraints，本质就是一份 **Tcl 脚本**，
里面调用 ``create_clock`` / ``set_input_delay`` / ``set_false_path`` 这些命令。

本模块实现：

1.  一个够用的 Tcl 词法/语法解析器（支持 ``{...}``、``"..."``、``[...]`` 命令替换）；
2.  ``expr`` 算术求值器 —— **刻意保留 Tcl 的整数除法语义**：
    ``[expr 1/75*1000]`` 得到 0（整数除法先算 1/75 = 0），
    而 ``[expr 1.0/75*1000]`` 才得到 13.333…。
    这是真实 SDC 里很容易写错、又很难 debug 的一类 bug；
3.  ``get_ports`` / ``get_pins`` / ``get_clocks`` / ``all_clocks`` 等对象查询；
4.  把命令翻译成 :class:`SdcDatabase`，供时序分析引擎消费。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

__all__ = [
    "SdcError",
    "ObjectRef",
    "ClockSpec",
    "InputDelaySpec",
    "OutputDelaySpec",
    "PathException",
    "MulticycleSpec",
    "SdcDatabase",
    "parse_sdc",
    "parse_sdc_file",
]


class SdcError(Exception):
    """SDC 语法/语义错误。"""


# 只有这些名字开头的 [...] 才当成命令替换；其它情况（如 FF3[*]/D）当字面量
_SUBST_CMD_RE = re.compile(
    r"\s*(?:get_ports|get_pins|get_clocks|get_cells|get_nets|get_lib_cells|"
    r"all_clocks|all_inputs|all_outputs|all_registers|"
    r"expr|list|llength|lindex|lsort|concat|string|format|set|clock)\b")


# ==========================================================================
# 1. Tcl 解析
# ==========================================================================


@dataclass
class Subst:
    """``[...]`` 命令替换。"""

    commands: list[list[object]]

    def __repr__(self) -> str:  # pragma: no cover
        return f"Subst({self.commands})"


def _balanced(text: str, i: int, opener: str, closer: str) -> tuple[str, int]:
    """从 ``text[i] == opener`` 开始，返回到匹配 closer 之前的内容和新的位置。"""
    assert text[i] == opener
    depth = 1
    j = i + 1
    start = j
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:j], j + 1
        j += 1
    raise SdcError(f"没有找到与 {text[i]!r} 匹配的 {closer!r}")


def _parse_commands(text: str) -> list[list[object]]:
    """把一段脚本切成若干条命令，每条命令是 word 列表。"""
    commands: list[list[object]] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n;":
            i += 1
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "\\" and i + 1 < n and text[i + 1] == "\n":
            i += 2
            continue

        words: list[object] = []
        while i < n and text[i] not in "\n;":
            ch = text[i]
            if ch in " \t\r":
                i += 1
                continue
            if ch == "#" and not words:
                break
            word, i = _parse_word(text, i)
            if word != "":
                words.append(word)
        if words:
            commands.append(words)
    return commands


def _parse_word(text: str, i: int) -> tuple[object, int]:
    """解析一个 word，可能由多段拼接而成（如 ``FF3[*]/D`` 或 ``a[get_pins x]b``）。"""
    parts: list[object] = []
    n = len(text)
    while i < n and text[i] not in " \t\r\n;":
        ch = text[i]
        if ch == "{":
            body, i = _balanced(text, i, "{", "}")
            parts.append(body)
        elif ch == '"':
            body, i = _balanced(text, i, '"', '"')
            parts.append(body)
        elif ch == "[":
            body, i = _balanced(text, i, "[", "]")
            if _SUBST_CMD_RE.match(body):
                parts.append(Subst(_parse_commands(body)))
            else:
                # FF3[*]/D 这种对象名里的通配不能当命令替换 ——
                # Tcl 会拿 * 去当命令名。整体当字面量才符合 SDC 的实际行为。
                parts.append("[" + body + "]")
        elif ch == "\\":
            if i + 1 < n:
                parts.append(text[i + 1])
                i += 2
            else:
                i += 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n;{}"[]\\':
                j += 1
            parts.append(text[i:j])
            i = j
    if len(parts) == 1:
        return parts[0], i
    return "".join(str(p) for p in parts), i


# ==========================================================================
# 2. expr 求值（保留 Tcl 整数除法语义）
# ==========================================================================


@dataclass(frozen=True)
class Num:
    """带"是否为整数"标记的精确有理数。"""

    value: Fraction
    is_int: bool = False

    @staticmethod
    def of(raw: str) -> "Num":
        raw = raw.strip()
        is_int = re.fullmatch(r"[+-]?\d+", raw) is not None
        return Num(Fraction(raw), is_int)

    def __float__(self) -> float:  # pragma: no cover
        return float(self.value)


_EXPR_TOKEN_RE = re.compile(
    r"\s*(?:(?P<num>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)"
    r"|(?P<op>[+\-*/()])"
    r"|(?P<ident>[A-Za-z_]\w*)"
    r"|(?P<other>.))",
    re.DOTALL,
)


class _ExprParser:
    """支持 + - * / 与括号，以及 ceil/floor/round/abs。"""

    def __init__(self, text: str) -> None:
        self.toks: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            m = _EXPR_TOKEN_RE.match(text, pos)
            if m is None:
                break
            pos = m.end()
            if m.lastgroup == "num":
                self.toks.append(("num", m.group()))
            elif m.lastgroup == "op":
                self.toks.append(("op", m.group()))
            elif m.lastgroup == "ident":
                self.toks.append(("ident", m.group()))
            elif not m.group().strip():
                continue
            else:
                raise SdcError(f"expr 里有无法处理的字符: {m.group()!r}")
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self) -> tuple[str, str]:
        t = self.peek()
        if t is None:
            raise SdcError("expr 表达式意外结束")
        self.i += 1
        return t

    def parse(self) -> Num:
        v = self.expr()
        if self.peek() is not None:
            raise SdcError(f"expr 解析结束后还有多余记号: {self.toks[self.i:]}")
        return v

    def expr(self) -> Num:
        left = self.term()
        while True:
            t = self.peek()
            if t and t[0] == "op" and t[1] in "+-":
                self.next()
                right = self.term()
                if t[1] == "+":
                    left = Num(left.value + right.value, left.is_int and right.is_int)
                else:
                    left = Num(left.value - right.value, left.is_int and right.is_int)
            else:
                return left

    def term(self) -> Num:
        left = self.factor()
        while True:
            t = self.peek()
            if t and t[0] == "op" and t[1] in "*/":
                self.next()
                right = self.factor()
                if t[1] == "*":
                    left = Num(left.value * right.value, left.is_int and right.is_int)
                else:
                    if right.value == 0:
                        raise SdcError("expr 里出现除以 0")
                    if left.is_int and right.is_int:
                        # ← Tcl 的整数除法：1/75 => 0
                        left = Num(Fraction(int(left.value / right.value)), True)
                    else:
                        left = Num(left.value / right.value, False)
            else:
                return left

    def factor(self) -> Num:
        kind, val = self.next()
        if kind == "op" and val == "(":
            v = self.expr()
            k, v2 = self.next()
            if not (k == "op" and v2 == ")"):
                raise SdcError("expr 括号不匹配")
            return v
        if kind == "op" and val == "-":
            v = self.factor()
            return Num(-v.value, v.is_int)
        if kind == "op" and val == "+":
            return self.factor()
        if kind == "num":
            return Num.of(val)
        if kind == "ident":
            fn = val
            k, v2 = self.next()
            if not (k == "op" and v2 == "("):
                raise SdcError(f"expr 里不认识的标识符 {fn!r}")
            arg = self.expr()
            k, v2 = self.next()
            if not (k == "op" and v2 == ")"):
                raise SdcError("expr 括号不匹配")
            import math

            x = float(arg.value)
            if fn == "ceil":
                return Num(Fraction(math.ceil(x)), True)
            if fn == "floor":
                return Num(Fraction(math.floor(x)), True)
            if fn == "round":
                return Num(Fraction(int(round(x))), True)
            if fn == "abs":
                return Num(abs(arg.value), arg.is_int)
            raise SdcError(f"expr 不支持函数 {fn!r}")
        raise SdcError(f"expr 无法解析 {val!r}")


def eval_expr(text: str) -> Num:
    return _ExprParser(text).parse()


# ==========================================================================
# 3. 对象查询
# ==========================================================================


@dataclass(frozen=True)
class ObjectRef:
    """一个被 SDC 选中的对象，例如 ``('port', 'A')`` / ``('pin', 'MUX0/I0')``。"""

    kind: str      # port / pin / clock / cell
    name: str

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.kind}:{self.name}"


class ObjectQuery:
    """提供 get_* 查询，需要知道设计里有哪些 port/pin/clock。"""

    def __init__(self) -> None:
        self.ports: set[str] = set()
        self.pins: set[str] = set()
        self.clocks: set[str] = set()

    def get(self, kind: str, pattern: str) -> list[ObjectRef]:
        pool = {"port": self.ports, "pin": self.pins, "clock": self.clocks, "cell": set()}[kind]
        if pattern == "*":
            return [ObjectRef(kind, n) for n in sorted(pool)]
        rx = re.compile("^" + re.escape(pattern).replace(r"\*", ".*") + "$")
        hits = sorted(n for n in pool if rx.match(n))
        if not hits and "*" not in pattern:
            # SDC 实践里 get_ports 找不到对象通常只是警告
            return []
        return [ObjectRef(kind, n) for n in hits]


# ==========================================================================
# 4. SDC 数据模型
# ==========================================================================


@dataclass
class ClockSpec:
    """一条 create_clock 的结果。"""

    name: str
    period: Fraction
    waveform: list[Fraction] = field(default_factory=list)   # [rise, fall, ...]
    source: str | None = None          # None 表示虚拟时钟
    comment: str = ""

    # set_clock_uncertainty
    unc_setup: Fraction = Fraction(0)
    unc_hold: Fraction = Fraction(0)
    unc_setup_rise: Fraction | None = None
    unc_setup_fall: Fraction | None = None
    unc_hold_rise: Fraction | None = None
    unc_hold_fall: Fraction | None = None

    # set_clock_transition
    transition_max: Fraction | None = None

    # set_clock_latency
    latency_source_max: Fraction = Fraction(0)
    latency_source_min: Fraction = Fraction(0)
    latency_network_max: Fraction = Fraction(0)
    latency_network_min: Fraction = Fraction(0)

    # set_propagated_clock
    propagated: bool = False

    # 约束历史，报告里会打印出来
    history: list[str] = field(default_factory=list)

    @property
    def is_virtual(self) -> bool:
        return self.source is None

    def rise_time(self) -> Fraction:
        return self.waveform[0] if self.waveform else Fraction(0)

    def fall_time(self) -> Fraction:
        return self.waveform[1] if len(self.waveform) > 1 else self.period / 2


@dataclass
class InputDelaySpec:
    port: str
    value_max: Fraction | None = None
    value_min: Fraction | None = None
    clock: str | None = None
    clock_fall: bool = False
    add_delay: bool = False
    rise: bool = True
    fall: bool = True
    history: str = ""


@dataclass
class OutputDelaySpec:
    port: str
    value_max: Fraction | None = None
    value_min: Fraction | None = None
    clock: str | None = None
    clock_fall: bool = False
    add_delay: bool = False
    rise: bool = True
    fall: bool = True
    history: str = ""


@dataclass
class PathFilter:
    """``-from / -through / -to`` 三个过滤条件。"""

    from_: list[ObjectRef] = field(default_factory=list)
    through: list[ObjectRef] = field(default_factory=list)
    to: list[ObjectRef] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.from_ or self.through or self.to)


@dataclass
class PathException:
    """set_false_path / set_max_delay / set_min_delay。"""

    kind: str                       # false_path / max_delay / min_delay
    filt: PathFilter
    value: Fraction | None = None
    setup: bool = True
    hold: bool = True
    history: str = ""


@dataclass
class MulticycleSpec:
    kind: str                       # setup / hold
    cycles: int
    filt: PathFilter
    history: str = ""


@dataclass
class ClockGroup:
    groups: list[list[str]] = field(default_factory=list)
    asynchronous: bool = True
    logically_exclusive: bool = False
    physically_exclusive: bool = False
    history: str = ""


@dataclass
class SdcDatabase:
    clocks: dict[str, ClockSpec] = field(default_factory=dict)
    input_delays: dict[str, list[InputDelaySpec]] = field(default_factory=dict)
    output_delays: dict[str, list[OutputDelaySpec]] = field(default_factory=dict)
    exceptions: list[PathException] = field(default_factory=list)
    multicycles: list[MulticycleSpec] = field(default_factory=list)
    clock_groups: list[ClockGroup] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ---- 便捷查询 -------------------------------------------------------
    def clock_of_port(self, port: str) -> str | None:
        """哪个时钟是通过 create_clock 定义在该端口上的。"""
        for c in self.clocks.values():
            if c.source == port:
                return c.name
        return None

    def input_delay(self, port: str) -> list[InputDelaySpec]:
        return self.input_delays.get(port, [])

    def output_delay(self, port: str) -> list[OutputDelaySpec]:
        return self.output_delays.get(port, [])

    def real_clocks(self) -> list[ClockSpec]:
        return [c for c in self.clocks.values() if not c.is_virtual]

    def virtual_clocks(self) -> list[ClockSpec]:
        return [c for c in self.clocks.values() if c.is_virtual]


# ==========================================================================
# 5. 命令解释器
# ==========================================================================

_FLAG_TAKING_VALUE = {
    "-name", "-period", "-waveform", "-clock", "-from", "-to", "-through",
    "-group", "-value", "-levels", "-rise_from", "-rise_to", "-fall_from",
    "-fall_to", "-reference_pin",
}

_NUM_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")


def _maybe_num(v: object) -> object:
    """SDC 里的数值都是"位置参数"，这里把长得像数字的词转成 :class:`Num`。"""
    if isinstance(v, str) and _NUM_RE.match(v):
        return Num.of(v)
    return v


def _split_flags(words: list[object], query: ObjectQuery) -> tuple[dict[str, list], list]:
    """把命令行拆成 (选项字典, 位置参数)。

    注意 SDC 的选项语义：``-max`` / ``-min`` / ``-setup`` / ``-hold`` / ``-rise``
    / ``-fall`` / ``-source`` / ``-add_delay`` 都是**布尔修饰符**，不接数值。
    数值（如 ``set_input_delay -max 7.4`` 里的 7.4）是位置参数。
    """
    opts: dict[str, list] = {}
    positional: list = []
    i = 0
    while i < len(words):
        w = words[i]
        if isinstance(w, str) and w.startswith("-"):
            if w in _FLAG_TAKING_VALUE:
                if i + 1 >= len(words):
                    raise SdcError(f"选项 {w} 缺少参数")
                opts.setdefault(w, []).append(_maybe_num(_resolve(words[i + 1], query)))
                i += 2
            else:
                opts.setdefault(w, []).append(True)
                i += 1
        else:
            resolved = _maybe_num(_resolve(w, query))
            # [get_ports clk] 会解析成一个列表，这里摊平，让位置参数直接是对象
            if isinstance(resolved, (list, tuple)):
                positional.extend(resolved)
            else:
                positional.append(resolved)
            i += 1
    return opts, positional


def _resolve(word: object, query: ObjectQuery) -> object:
    """执行 ``[...]`` 命令替换，得到 str / Num / list[ObjectRef]。"""
    if isinstance(word, Subst):
        return _eval_subst(word, query)
    return word


def _eval_subst(subst: Subst, query: ObjectQuery) -> object:
    result: object = None
    for cmd in subst.commands:
        result = _eval_command(cmd, query, want_value=True)
    return result


def _eval_command(cmd: list[object], query: ObjectQuery, want_value: bool = False) -> object:
    if not cmd:
        return None
    head = cmd[0]
    if not isinstance(head, str):
        raise SdcError(f"命令名必须是字符串: {head!r}")
    args = cmd[1:]

    if head == "expr":
        text = " ".join(str(_resolve(a, query)) for a in args)
        return eval_expr(text)

    if head in ("get_ports", "get_pins", "get_clocks", "get_cells", "all_clocks"):
        if head == "all_clocks":
            return [ObjectRef("clock", n) for n in sorted(query.clocks)]
        kind = {"get_ports": "port", "get_pins": "pin",
                "get_clocks": "clock", "get_cells": "cell"}[head]
        out: list[ObjectRef] = []
        for a in args:
            if isinstance(a, str) and a.startswith("-"):
                continue
            val = _resolve(a, query)
            for pattern in (val if isinstance(val, (list, tuple)) else [val]):
                if isinstance(pattern, ObjectRef):
                    out.append(pattern)
                else:
                    out.extend(query.get(kind, str(pattern)))
        return out

    raise SdcError(f"不支持的 SDC 命令: {head!r}")


def _as_fraction(v: object) -> Fraction:
    if isinstance(v, Num):
        return v.value
    if isinstance(v, (int, Fraction)):
        return Fraction(v)
    if isinstance(v, float):
        return Fraction(str(v))
    if isinstance(v, str):
        return Num.of(v).value
    raise SdcError(f"期望数值，实际得到 {v!r}")


def _refs(v: object, kind: str | None = None) -> list[ObjectRef]:
    if v is None:
        return []
    if isinstance(v, ObjectRef):
        return [v]
    if isinstance(v, (list, tuple)):
        out: list[ObjectRef] = []
        for x in v:
            out.extend(_refs(x, kind))
        return out
    if isinstance(v, str):
        if "/" in v:                       # 形如 FF1/CP 的引脚名
            return [ObjectRef("pin", v)]
        return [ObjectRef(kind or "unknown", v)]
    return []


def _strlist(v: object) -> list[str]:
    if v is None:
        return []
    if isinstance(v, ObjectRef):
        return [v.name]
    if isinstance(v, (list, tuple)):
        out: list[str] = []
        for x in v:
            out.extend(_strlist(x))
        return out
    if isinstance(v, str):
        # {clka clkb} 这种花括号列表会带着空格进来
        return v.split()
    return [str(v)]


def _apply(cmd: list[object], db: SdcDatabase, query: ObjectQuery) -> None:
    head = cmd[0]
    assert isinstance(head, str)
    opts, pos = _split_flags(cmd[1:], query)
    raw = " ".join(_render(w) for w in cmd)

    # ---------------------------------------------------------------- clock
    if head == "create_clock":
        period = opts.get("-period", [None])[0]
        if period is None:
            raise SdcError("create_clock 必须给 -period")
        period_f = _as_fraction(period)
        srcs = [p for p in pos if isinstance(p, ObjectRef)]
        names = opts.get("-name")
        if names:
            name = _strlist(names[0])[0]
            source = srcs[0].name if srcs else None
        elif srcs:
            name = srcs[0].name
            source = srcs[0].name
        else:
            raise SdcError("create_clock 需要 -name 或源端口（虚拟时钟必须给 -name）")

        waveform: list[Fraction] = []
        if "-waveform" in opts:
            raw_wf = opts["-waveform"][0]
            waveform = [_as_fraction(x) for x in _strlist(raw_wf)]
        if not waveform:
            waveform = [Fraction(0), period_f / 2]

        clk = ClockSpec(name=name, period=period_f, waveform=waveform,
                        source=source, comment="虚拟时钟" if source is None else "")
        clk.history.append(raw)
        db.clocks[name] = clk
        query.clocks.add(name)
        db.log.append(f"create_clock  -> {name}  period={_fmt(period_f)}ns"
                      + (f"  source={source}" if source else "  (virtual)"))
        return

    if head == "set_clock_uncertainty":
        val = next((p.value for p in pos if isinstance(p, Num)), None)
        if val is None:
            raise SdcError("set_clock_uncertainty 缺少数值")
        targets = _strlist(opts.get("-clock")) or _strlist([
            p for p in pos if isinstance(p, ObjectRef)])
        app_setup = "-hold" not in opts
        app_hold = "-setup" not in opts
        for name in (targets or list(db.clocks)):
            clk = db.clocks.get(name)
            if clk is None:
                db.warnings.append(f"set_clock_uncertainty: 找不到时钟 {name}")
                continue
            if app_setup:
                clk.unc_setup = val
                if "-rise" in opts:
                    clk.unc_setup_rise = val
                if "-fall" in opts:
                    clk.unc_setup_fall = val
            if app_hold:
                clk.unc_hold = val
                if "-rise" in opts:
                    clk.unc_hold_rise = val
                if "-fall" in opts:
                    clk.unc_hold_fall = val
            clk.history.append(raw)
        db.log.append(f"set_clock_uncertainty -> {', '.join(targets)} = {_fmt(val)}ns")
        return

    if head == "set_clock_transition":
        val = next((p.value for p in pos if isinstance(p, Num)), None)
        if val is None:
            raise SdcError("set_clock_transition 缺少数值")
        targets = _strlist(opts.get("-clock"))
        for name in (targets or list(db.clocks)):
            clk = db.clocks.get(name)
            if clk is not None:
                clk.transition_max = val
                clk.history.append(raw)
        db.log.append(f"set_clock_transition -> {', '.join(targets)} = {_fmt(val)}ns")
        return

    if head == "set_clock_latency":
        val = next((p.value for p in pos if isinstance(p, Num)), None)
        if val is None:
            raise SdcError("set_clock_latency 缺少数值")
        targets = _strlist(opts.get("-clock"))
        for name in (targets or list(db.clocks)):
            clk = db.clocks.get(name)
            if clk is None:
                continue
            if "-source" in opts:
                clk.latency_source_max = max(clk.latency_source_max, val)
            else:
                clk.latency_network_max = val
            clk.history.append(raw)
        db.log.append(f"set_clock_latency -> {', '.join(targets)} = {_fmt(val)}ns"
                      f"{'  (source/插入延迟)' if '-source' in opts else '  (网络延迟)'}")
        return

    if head == "set_propagated_clock":
        targets = _strlist([p for p in pos if isinstance(p, ObjectRef)]) or _strlist(opts.get("-clock"))
        for name in (targets or list(db.clocks)):
            clk = db.clocks.get(name)
            if clk is not None:
                clk.propagated = True
                clk.latency_network_max = Fraction(0)
                clk.history.append(raw)
        db.log.append(f"set_propagated_clock -> {', '.join(targets)}"
                      "  (布线后由实际时钟树计算网络延迟)")
        return

    # ------------------------------------------------------------ i/o delay
    if head in ("set_input_delay", "set_output_delay"):
        val = next((p.value for p in pos if isinstance(p, Num)), None)
        if val is None:
            raise SdcError(f"{head} 缺少数值")
        ports = _strlist([p for p in pos if isinstance(p, ObjectRef)])
        if not ports:
            raise SdcError(f"{head} 没有指定端口")
        clk = _strlist(opts.get("-clock"))
        close_clk = clk[0] if clk else None
        is_max = "-min" not in opts or "-max" in opts
        is_min = "-min" in opts

        for port in ports:
            if head == "set_input_delay":
                spec = InputDelaySpec(
                    port=port, clock=close_clk, clock_fall="-clock_fall" in opts,
                    add_delay="-add_delay" in opts,
                    rise="-fall" not in opts, fall="-rise" not in opts, history=raw)
                if is_max:
                    spec.value_max = val
                if is_min:
                    spec.value_min = val
                db.input_delays.setdefault(port, []).append(spec)
            else:
                spec = OutputDelaySpec(
                    port=port, clock=close_clk, clock_fall="-clock_fall" in opts,
                    add_delay="-add_delay" in opts,
                    rise="-fall" not in opts, fall="-rise" not in opts, history=raw)
                if is_max:
                    spec.value_max = val
                if is_min:
                    spec.value_min = val
                db.output_delays.setdefault(port, []).append(spec)

            # -add_delay 的语义：没有它就会覆盖同端口同方向的旧约束
            if "-add_delay" not in opts:
                lst = (db.input_delays if head == "set_input_delay"
                       else db.output_delays)[port]
                if len(lst) > 1:
                    keep = lst[-1]
                    lst.clear()
                    lst.append(keep)
                    db.warnings.append(
                        f"{head} {port}: 未加 -add_delay，前面的约束已被覆盖")

        db.log.append(
            f"{head} -> {', '.join(ports)} = {_fmt(val)}ns"
            f"{'  -clock ' + close_clk if close_clk else ''}"
            f"{'  -add_delay' if '-add_delay' in opts else ''}")
        return

    # ------------------------------------------------------------ exceptions
    if head in ("set_false_path", "set_max_delay", "set_min_delay"):
        filt = PathFilter(
            from_=_refs(opts.get("-from")),
            through=_refs(opts.get("-through")),
            to=_refs(opts.get("-to")),
        )
        value = None
        if head != "set_false_path":
            value = next((p.value for p in pos if isinstance(p, Num)), None)
        exc = PathException(
            kind={"set_false_path": "false_path", "set_max_delay": "max_delay",
                  "set_min_delay": "min_delay"}[head],
            filt=filt, value=value,
            setup="-hold" not in opts, hold="-setup" not in opts, history=raw)
        db.exceptions.append(exc)
        db.log.append(f"{head}: from={[str(r) for r in filt.from_]}"
                      f" through={[str(r) for r in filt.through]}"
                      f" to={[str(r) for r in filt.to]}"
                      + (f" value={_fmt(value)}" if value is not None else ""))
        return

    if head == "set_clock_groups":
        cg = ClockGroup(
            asynchronous="-asynchronous" in opts,
            logically_exclusive="-logically_exclusive" in opts,
            physically_exclusive="-physically_exclusive" in opts,
            history=raw)
        for g in opts.get("-group", []):
            cg.groups.append(_strlist(g))
        db.clock_groups.append(cg)
        db.log.append("set_clock_groups -> " +
                      " | ".join("{" + " ".join(g) + "}" for g in cg.groups))
        return

    if head == "set_multicycle_path":
        cycles = None
        for p in pos:
            if isinstance(p, Num):
                cycles = int(p.value)
        if cycles is None:
            cycles = 1
        kind = "hold" if "-hold" in opts else "setup"
        filt = PathFilter(
            from_=_refs(opts.get("-from")),
            through=_refs(opts.get("-through")),
            to=_refs(opts.get("-to")),
        )
        db.multicycles.append(MulticycleSpec(kind=kind, cycles=cycles,
                                             filt=filt, history=raw))
        db.log.append(f"set_multicycle_path -{kind} {cycles}: "
                      f"from={[str(r) for r in filt.from_]} "
                      f"through={[str(r) for r in filt.through]} "
                      f"to={[str(r) for r in filt.to]}")
        return

    if head in ("set_load", "set_driving_cell", "set_wire_load_model",
                "set_max_area", "set_max_fanout", "set_max_transition",
                "set_min_delay", "set_units", "set_operating_conditions",
                "set_dont_touch", "set_size_only", "set_ideal_network"):
        db.log.append(f"(忽略) {head}")
        return

    raise SdcError(f"不支持的 SDC 命令: {head!r}")


def _render(w: object) -> str:
    if isinstance(w, Subst):
        return "[" + "; ".join(" ".join(_render(x) for x in c) for c in w.commands) + "]"
    return str(w)


def _to_num(v: object) -> Fraction:
    return _as_fraction(v)


def _fmt(v: Fraction | None) -> str:
    if v is None:
        return "-"
    return f"{float(v):g}"


# ==========================================================================


def parse_sdc(text: str, query: ObjectQuery | None = None) -> SdcDatabase:
    """解析 SDC 文本。``query`` 提供设计里的对象名，用于 get_ports/get_pins。"""
    db = SdcDatabase()
    query = query or ObjectQuery()
    commands = _parse_commands(text)
    for cmd in commands:
        try:
            _apply(cmd, db, query)
        except SdcError:
            raise
        except Exception as exc:  # pragma: no cover
            raise SdcError(f"处理命令失败: {_render(cmd[0])} ... ({exc})") from exc
    return db


def parse_sdc_file(path, query: ObjectQuery | None = None) -> SdcDatabase:
    return parse_sdc(Path(path).read_text(encoding="utf-8"), query)
