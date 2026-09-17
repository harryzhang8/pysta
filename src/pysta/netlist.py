# -*- coding: utf-8 -*-
"""
结构化 Verilog 网表解析器（工艺映射后的门级网表）
================================================

STA 是**基于网表**做的，不是基于 RTL。所以我们先把综合后的门级网表
（`netlist/*.v`）读进来，才能建时序图。

本解析器覆盖的是工艺映射后网表实际会用到的那一小撮语法：

    module top (clk, a, b, y);
      input  clk, a, b;
      output y;
      wire   n1, n2;
      DFFRQ  ff1 (.CK(clk), .D(n1), .Q(q1));
      NAND2X1 u1 (.A(a), .B(b), .Y(n1));     // 也支持位置连接: NAND2X1 u1 (a, b, n1);
      assign  y = q1;                         // 只做网络别名，不引入单元
    endmodule

总线的处理：声明 `input [7:0] A;` 会展开成 A[7]..A[0] 这些独立网络，
连接里的 `.A0(A[0])` / `.A(A)`（整条总线）都能识别。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["NetlistError", "Instance", "Module", "parse_verilog", "parse_verilog_file"]

# Verilog 关键字，用来区分"单元例化"和声明语句
_KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "assign",
    "parameter", "localparam", "always", "initial", "begin", "end", "if", "else",
    "case", "endcase", "posedge", "negedge", "generate", "endgenerate", "for",
    "integer", "genvar", "supply0", "supply1", "tri", "default", "defparam",
    "specify", "endspecify", "function", "endfunction", "task", "endtask",
}


class NetlistError(Exception):
    """网表语法/语义错误。"""


# --------------------------------------------------------------------------


@dataclass
class Instance:
    """一次单元例化。"""

    name: str                    # 例化名，如 ff1 / u1
    cell: str                    # 引用的单元名，如 DFFRQ
    conns: dict[str, str] = field(default_factory=dict)   # pin -> net

    def net(self, pin: str) -> str | None:
        return self.conns.get(pin)


@dataclass
class Module:
    name: str
    ports: dict[str, str] = field(default_factory=dict)       # port -> direction
    nets: set[str] = field(default_factory=set)
    instances: list[Instance] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)     # net -> net

    def resolve(self, net: str) -> str:
        """沿着 assign 别名链找到真正的网络名。"""
        seen = set()
        cur = net
        while cur in self.aliases and cur not in seen:
            seen.add(cur)
            cur = self.aliases[cur]
        return cur


# --------------------------------------------------------------------------
# 预处理：去掉注释、合并续行
# --------------------------------------------------------------------------


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", " ", text)
    return text


def _split_statements(text: str) -> list[str]:
    """按 ';' 切分，但忽略括号内部的 ';'（一般不会出现，稳妥起见）。"""
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == ";" and depth <= 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


_BUS_RE = re.compile(r"^([A-Za-z_]\w*)\s*\[\s*(\d+)\s*(?::\s*(\d+)\s*)?\]$")


def _expand_decl(name: str) -> list[str]:
    m = _BUS_RE.match(name)
    if not m:
        return [name]
    base, msb, lsb = m.group(1), int(m.group(2)), m.group(3)
    lsb = int(lsb) if lsb is not None else 0
    lo, hi = min(msb, lsb), max(msb, lsb)
    return [f"{base}[{i}]" for i in range(lo, hi + 1)]


def _expand_net_expr(expr: str, declared_buses: dict[str, list[str]]) -> list[str]:
    """把连接里的 `.A(A)` / `.A0(A[0])` 展开成具体网络。"""
    expr = expr.strip()
    if expr in declared_buses:
        return list(declared_buses[expr])
    return [expr]


# --------------------------------------------------------------------------


def parse_verilog(text: str) -> Module:
    """解析单个 module 的源码，返回 :class:`Module`。"""
    text = _strip_comments(text)
    m = re.search(r"\bmodule\s+([A-Za-z_]\w*)", text)
    if not m:
        raise NetlistError("没有找到 module 声明")
    module = Module(name=m.group(1))

    body_start = text.find(";", m.end())
    body = text[body_start + 1:] if body_start >= 0 else ""
    end = body.rfind("endmodule")
    if end >= 0:
        body = body[:end]

    declared_buses: dict[str, list[str]] = {}

    for stmt in _split_statements(body):
        head = stmt.split(None, 1)[0] if stmt.split() else ""

        # ---- 声明 -------------------------------------------------------
        if head in ("input", "output", "inout"):
            m = re.match(r"^\w+\s*(\[[^\]]*\])?\s*(.*)$", stmt, re.DOTALL)
            if not m:
                continue
            rng, names = m.group(1) or "", m.group(2)
            direction = head
            for item in names.split(","):
                item = item.strip()
                if not item:
                    continue
                # input [1:0] A;   ->   A[1:0]  ->  A[1], A[0]
                full = item + rng
                bus = _BUS_RE.match(full)
                if bus:
                    nets = _expand_decl(full)
                    declared_buses[bus.group(1)] = nets
                else:
                    nets = [item]
                for n in nets:
                    module.ports[n] = direction
                    module.nets.add(n)
            continue

        if head in ("wire", "reg", "tri"):
            m = re.match(r"^\w+\s*(\[[^\]]*\])?\s*(.*)$", stmt, re.DOTALL)
            if not m:
                continue
            rng, names = m.group(1) or "", m.group(2)
            for item in names.split(","):
                item = item.strip()
                if not item:
                    continue
                full = item + rng
                bus = _BUS_RE.match(full)
                if bus:
                    declared_buses[bus.group(1)] = _expand_decl(full)
                module.nets.update(_expand_decl(full))
            continue

        if head in ("parameter", "localparam", "integer", "genvar"):
            continue

        # ---- assign（只做别名）------------------------------------------
        if head == "assign":
            rhs = stmt[len("assign"):]
            if "=" not in rhs:
                continue
            lhs, r = rhs.split("=", 1)
            lhs_t = lhs.strip()
            rhs_t = r.strip()
            # assign {..} / 带运算符的一律跳过（门级网表里不该出现）
            if re.fullmatch(r"[A-Za-z_]\w*(?:\[\d+\])?", lhs_t) and \
               re.fullmatch(r"[A-Za-z_]\w*(?:\[\d+\])?", rhs_t):
                for a in _expand_net_expr(lhs_t, declared_buses):
                    for b in _expand_net_expr(rhs_t, declared_buses):
                        module.aliases[a] = b
                        module.nets.add(a)
                        module.nets.add(b)
            continue

        if head in ("always", "initial", "generate", "for", "if", "case"):
            raise NetlistError(
                f"这是 RTL 不是网表：出现行为级语句 {head!r}。"
                "请把 RTL 综合成门级网表后再做 STA。"
            )

        # ---- 单元例化 ---------------------------------------------------
        inst = _parse_instance(stmt, declared_buses)
        if inst is not None:
            module.instances.append(inst)
            for net in inst.conns.values():
                module.nets.add(net)

    return module


_INST_RE = re.compile(
    r"^(?P<cell>[A-Za-z_]\w*)\s+(?P<params>#\s*\([^)]*\)\s*)?"
    r"(?P<inst>[A-Za-z_]\w*(?:\[\d+\])?)\s*\((?P<conns>.*)\)\s*$",
    re.DOTALL,
)


def _parse_instance(stmt: str, declared_buses: dict[str, list[str]]) -> Instance | None:
    m = _INST_RE.match(stmt.strip())
    if m is None:
        if stmt.strip():
            raise NetlistError(f"无法解析的语句: {stmt.strip()[:80]!r}")
        return None
    cell = m.group("cell")
    if cell in _KEYWORDS:
        return None
    inst_name = m.group("inst")
    conns_text = m.group("conns").strip()

    conns: dict[str, str] = {}
    if not conns_text:
        return Instance(inst_name, cell, conns)

    # 命名连接  .PIN(net) ；否则位置连接
    named = re.findall(r"\.\s*([A-Za-z_]\w*)\s*\(\s*([^)]*)\)", conns_text)
    if named:
        for pin, net in named:
            nets = _expand_net_expr(net.strip(), declared_buses)
            conns[pin] = nets[0]
    else:
        for idx, net in enumerate(conns_text.split(",")):
            conns[f"__pos{idx}"] = net.strip()
    return Instance(inst_name, cell, conns)


def parse_verilog_file(path) -> Module:
    return parse_verilog(Path(path).read_text(encoding="utf-8"))
