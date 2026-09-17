# -*- coding: utf-8 -*-
"""
迷你静态时序分析引擎
====================

把 `工艺库 + 门级网表 + SDC` 三者合起来，算出每条时序路径的

    数据到达时间  T_arrival
    数据需求时间  T_required
    时间裕量      slack = T_required - T_arrival

并额外给出**最大允许组合逻辑延时**（timing budget）：

    budget = T_required - (发送沿 + 发送时钟延迟 + 起点代价)

于是 ``slack = budget - 实际组合延时``。相比 slack，budget 与具体走线无关，
只跟时钟沿关系有关，更适合当"这条路径被约束到了多少 ns"的直觉指标。

时钟沿关系
----------
设发送沿在 t_L，捕获时钟在 t_L 之后第一个上升沿的序号为 i0，则

    setup 捕获沿序号 = i0 + (M_setup - 1)
    hold  捕获沿序号 = setup 捕获沿序号 - 1 - M_hold

默认 M_setup = 1、M_hold = 0，于是 setup 在下一沿、hold 在同一沿 —— 这就是
"保持时间分析比建立时间分析提前一个时钟周期沿"。

代入 ``set_multicycle_path -setup 6``（M_setup=6, M_hold=0），周期 10ns：
setup 沿到 i0+5 = 60ns，而 hold 沿会跟着挪到 i0+4 = 50ns；
再补一条 ``set_multicycle_path -hold 5``，hold 沿变成 i0+5-1-5 = 0ns。
**"写了 -setup 就必须补 -hold"** 是这里最容易错的地方，
``tests/test_timing.py`` 与 ``examples/sdc/ex9b_*.sdc`` 都专门覆盖了它。

数值精度
--------
所有时间量用 ``fractions.Fraction`` 表示，因此 75MHz 这类周期（40/3 ns）
是**精确**的，公共基本周期不会出现 ``13.3333 * 3 = 39.9999`` 的浮点误差。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .liberty import Cell, Library, Table, parse_liberty_file
from .netlist import Module, parse_verilog_file
from .sdc import (
    ClockSpec,
    ObjectQuery,
    PathException,
    PathFilter,
    SdcDatabase,
    parse_sdc_file,
)

__all__ = ["Design", "TimingPath", "PathTiming", "AnalysisResult", "analyze"]

ZERO = Fraction(0)


# ==========================================================================
# 时钟沿
# ==========================================================================


def lcm_frac(a: Fraction, b: Fraction) -> Fraction:
    """两个有理数周期的最小公倍数 —— 即 DC 说的 common base period。

        LCM(p1/q1, p2/q2) = lcm(p1, p2) / gcd(q1, q2)

    例：周期 20ns 与 40/3ns(=75MHz) 的公共基本周期 = 40ns；
        周期 30ns 与 20ns         的公共基本周期 = 60ns。
    """
    num = math.lcm(a.numerator, b.numerator)
    den = math.gcd(a.denominator, b.denominator)
    return Fraction(num, den)


@dataclass
class ClockEdges:
    """给一个时钟加上沿序号访问能力。"""

    spec: ClockSpec

    def rise(self, k: int) -> Fraction:
        return self.spec.rise_time() + k * self.spec.period


def first_rise_index_after(edges: ClockEdges, t: Fraction) -> int:
    """返回第一个严格大于 t 的上升沿序号。"""
    r0 = edges.spec.rise_time()
    T = edges.spec.period
    if T == 0:
        return 0
    return math.floor((t - r0) / T) + 1


# ==========================================================================
# 数据结构
# ==========================================================================


@dataclass
class TimingArc:
    src: str
    dst: str
    delay: Fraction
    kind: str          # net / comb / clk2q  /  input / output
    cell: str = ""


@dataclass
class TimingPath:
    """一条时序路径（startpoint -> endpoint）。"""

    startpoint: str
    endpoint: str
    kind: str                       # input->reg / reg->reg / reg->output / input->output
    nodes: list[str] = field(default_factory=list)
    arcs: list[TimingArc] = field(default_factory=list)

    comb_delay: Fraction = ZERO     # 组合逻辑总延时（不含起点代价）
    launch_cost: Fraction = ZERO    # 触发器 Tco 或输入端口 input_delay
    launch_cost_name: str = ""

    launch_clock: str | None = None
    capture_clock: str | None = None

    setup_disabled: str | None = None    # 非 None 表示被 false path / clock group 屏蔽
    hold_disabled: str | None = None
    max_delay_limit: Fraction | None = None

    def node_set(self) -> set[str]:
        return set(self.nodes)


@dataclass
class PathTiming:
    """一条路径的完整时序分析结果。"""

    path: TimingPath
    launch_edge: Fraction = ZERO
    capture_edge_setup: Fraction | None = None
    capture_edge_hold: Fraction | None = None

    m_setup: int = 1
    m_hold: int = 0

    @property
    def comb_delay(self) -> Fraction:
        return self.path.comb_delay

    @property
    def launch_cost(self) -> Fraction:
        return self.path.launch_cost

    @property
    def budget_setup(self) -> Fraction | None:
        """最大允许组合延时（建立时间角度）= 实际组合延时 + 建立裕量。

        slack = T_required - T_arrival = (预算 - 组合延时)
        =>  预算 = 组合延时 + slack
        """
        if self.slack_setup is None:
            return None
        return self.comb_delay + self.slack_setup

    @property
    def budget_hold(self) -> Fraction | None:
        """保持时间角度允许的**最小**组合延时 = 组合延时 - 保持裕量。"""
        if self.slack_hold is None:
            return None
        return self.comb_delay - self.slack_hold

    # -- 由 analyze() 填充 -------------------------------------------------
    arr_base: Fraction = ZERO          # 发送沿 + 发送时钟延迟 + 起点代价
    arr: Fraction = ZERO               # 数据到达时间
    req_setup: Fraction | None = None  # 数据需求时间（建立）
    slack_setup: Fraction | None = None
    req_hold: Fraction | None = None
    slack_hold: Fraction | None = None
    note: str = ""

    @property
    def verdict(self) -> str:
        if self.path.setup_disabled:
            return "EXCLUDED"
        if self.slack_setup is None:
            return "UNCONSTRAINED"
        return "MET" if self.slack_setup >= 0 else "VIOLATED"


@dataclass
class AnalysisResult:
    design_name: str
    paths: list[PathTiming] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[PathTiming]:
        return [p for p in self.paths if p.path.kind == kind]

    def constrained(self) -> list[PathTiming]:
        return [p for p in self.paths if p.slack_setup is not None
                and not p.path.setup_disabled]

    def worst_setup(self, n: int = 10) -> list[PathTiming]:
        ps = self.constrained()
        return sorted(ps, key=lambda p: (p.slack_setup, p.path.startpoint))[:n]

    def worst_hold(self) -> list[PathTiming]:
        ps = [p for p in self.paths if p.slack_hold is not None
              and not p.path.hold_disabled]
        return sorted(ps, key=lambda p: (p.slack_hold, p.path.startpoint))

    def violations(self) -> list[PathTiming]:
        """建立时间违例。"""
        return [p for p in self.constrained() if p.slack_setup < 0]

    def hold_violations(self) -> list[PathTiming]:
        """保持时间违例。"""
        return [p for p in self.paths
                if p.slack_hold is not None and p.slack_hold < 0
                and not p.path.hold_disabled]


# ==========================================================================
# 设计
# ==========================================================================


class WireLoadModel:
    """线负载模型：net_delay = base + per_fanout * fanout。

    默认为全 0，即"理想连线 / 布线前零线延时"。
    示例里的手算数字都是在理想连线下得到的，先把连线变量固定住，
    才能把注意力放在时钟沿关系上。
    """

    def __init__(self, base: Fraction = ZERO, per_fanout: Fraction = ZERO) -> None:
        self.base = base
        self.per_fanout = per_fanout

    def delay(self, fanout: int) -> Fraction:
        return self.base + self.per_fanout * fanout


@dataclass
class Design:
    top: str = "top"
    module: Module | None = None
    lib: Library | None = None
    sdc: SdcDatabase | None = None
    query: ObjectQuery | None = None
    wire_load: WireLoadModel = field(default_factory=WireLoadModel)
    default_slew: Fraction = Fraction(1, 50)          # 0.02ns，对齐工艺库 index_1
    lib_path: Path | None = None
    netlist_path: Path | None = None
    sdc_path: Path | None = None

    # 运行期索引 ---------------------------------------------------------
    _net_drivers: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    _net_sinks: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    _net_load: dict[str, Fraction] = field(default_factory=dict)
    _clk_of_net: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ 构建
    @classmethod
    def build(cls, lib_path, netlist_path, sdc_path=None, **kw) -> "Design":
        d = cls(
            lib=parse_liberty_file(lib_path),
            module=parse_verilog_file(netlist_path),
            lib_path=Path(lib_path),
            netlist_path=Path(netlist_path),
        )
        d.top = d.module.name
        for k, v in kw.items():
            setattr(d, k, v)
        d._index()
        if sdc_path is not None:
            d.apply_sdc(sdc_path)
        return d

    def apply_sdc(self, sdc_path) -> SdcDatabase:
        self.query = ObjectQuery()
        self.query.ports = {p for p, d in self.module.ports.items() if d in ("input", "output")}
        for inst in self.module.instances:
            for pin in inst.conns:
                self.query.pins.add(f"{inst.name}/{pin}")
        if self.sdc is not None:
            self.query.clocks = set(self.sdc.clocks)
        self.sdc_path = Path(sdc_path)
        self.sdc = parse_sdc_file(sdc_path, self.query)
        self._resolve_clock_networks()
        return self.sdc

    # ------------------------------------------------------------- 内部索引
    def _index(self) -> None:
        m, lib = self.module, self.lib
        for net in m.nets:
            self._net_drivers[net] = []
            self._net_sinks[net] = []
            self._net_load[net] = ZERO

        for name, direction in m.ports.items():
            if direction == "input":
                self._net_drivers.setdefault(name, []).append(("@port", name))
            else:
                self._net_sinks.setdefault(name, []).append(("@port", name))

        for inst in m.instances:
            cell = lib.cell(inst.cell)
            for pin_name, net_name in inst.conns.items():
                net = m.resolve(net_name)
                pin = cell.pins.get(pin_name)
                node = f"{inst.name}/{pin_name}"
                if pin is None:
                    continue
                if pin.direction == "output":
                    self._net_drivers.setdefault(net, []).append((inst.name, pin_name))
                else:
                    self._net_sinks.setdefault(net, []).append((inst.name, pin_name))
                    self._net_load[net] = self._net_load.get(net, ZERO) + pin.capacitance

    def _cell_of(self, inst_name: str) -> Cell:
        inst = next(i for i in self.module.instances if i.name == inst_name)
        return self.lib.cell(inst.cell)

    def _inst_of(self, inst_name: str):
        return next(i for i in self.module.instances if i.name == inst_name)

    # --------------------------------------------------------- 时钟网络传播
    def _resolve_clock_networks(self) -> None:
        if self.sdc is None:
            return
        m = self.module
        for clk in self.sdc.clocks.values():
            if clk.source:
                self._clk_of_net[m.resolve(clk.source)] = clk.name
        # 穿过缓冲器/反相器继续传播
        changed = True
        while changed:
            changed = False
            for inst in m.instances:
                cell = self.lib.cell(inst.cell)
                if cell.is_sequential:
                    continue
                data_ins = [p.name for p in cell.pins.values()
                            if p.direction == "input" and p.name != cell.clock_pin]
                outs = [p.name for p in cell.pins.values() if p.direction == "output"]
                if len(data_ins) != 1 or len(outs) != 1:
                    continue
                in_net = inst.conns.get(data_ins[0])
                out_net = inst.conns.get(outs[0])
                if in_net is None or out_net is None:
                    continue
                in_net = m.resolve(in_net)
                out_net = m.resolve(out_net)
                if in_net in self._clk_of_net and out_net not in self._clk_of_net:
                    self._clk_of_net[out_net] = self._clk_of_net[in_net]
                    changed = True

    def clock_of_instance(self, inst_name: str) -> str | None:
        cell = self._cell_of(inst_name)
        if not cell.is_sequential or not cell.clock_pin:
            return None
        inst = self._inst_of(inst_name)
        net = inst.conns.get(cell.clock_pin)
        if net is None:
            return None
        return self._clk_of_net.get(self.module.resolve(net))

    def clock_of_ff_pin(self, pin_node: str) -> str | None:
        """``FF2/CP`` -> 时钟名。"""
        if "/" not in pin_node:
            return None
        return self.clock_of_instance(pin_node.split("/", 1)[0])

    def is_ff_clock_pin(self, node: str) -> bool:
        if "/" not in node:
            return False
        inst_name, pin = node.split("/", 1)
        cell = self._cell_of(inst_name)
        return cell.is_sequential and pin == cell.clock_pin

    def is_ff_data_pin(self, node: str) -> bool:
        if "/" not in node:
            return False
        inst_name, pin = node.split("/", 1)
        cell = self._cell_of(inst_name)
        return cell.is_sequential and pin == cell.next_state_pin

    def ff_of_data_pin(self, node: str) -> str | None:
        return node.split("/", 1)[0] if self.is_ff_data_pin(node) else None

    # ------------------------------------------------------------- 延时计算
    def net_delay(self, net: str) -> Fraction:
        return self.wire_load.delay(len(self._net_sinks.get(net, [])))

    def comb_arc_delay(self, inst_name: str, in_pin: str, out_pin: str,
                       slew: Fraction | None = None) -> Fraction:
        cell = self._cell_of(inst_name)
        arc = next((a for a in cell.arcs
                    if a.related_pin == in_pin and a.output_pin == out_pin), None)
        if arc is None:
            return ZERO
        inst = self._inst_of(inst_name)
        out_net = self.module.resolve(inst.conns.get(out_pin, ""))
        load = self._net_load.get(out_net, ZERO)
        return arc.delay(slew if slew is not None else self.default_slew, load)

    def clk_to_q_delay(self, inst_name: str) -> Fraction:
        """CP -> Q 的翻转延时 Tco，和组合单元一样按 Q 端实际负载查表。"""
        cell = self._cell_of(inst_name)
        inst = self._inst_of(inst_name)
        load = ZERO
        if cell.output_pins:
            net = self.module.resolve(inst.conns.get(cell.output_pins[0], ""))
            load = self._net_load.get(net, ZERO)
        return cell.clk_to_q.lookup(self.default_slew, load)


# ==========================================================================
# 时序图遍历
# ==========================================================================


def _out_nodes(design: Design, node: str) -> list[TimingArc]:
    """返回从 node 出发的所有时序弧。"""
    m = design.module
    out: list[TimingArc] = []

    if node.startswith("@port:"):
        net = node.split(":", 1)[1]
        for inst_name, pin in design._net_sinks.get(net, []):
            if inst_name == "@port":
                continue
            out.append(TimingArc(node, f"{inst_name}/{pin}",
                                 design.net_delay(net), "net"))
        return out

    inst_name, pin = node.split("/", 1)
    cell = design._cell_of(inst_name)
    pin_def = cell.pins.get(pin)

    if pin_def is None:
        return out

    if pin_def.direction == "input":
        if cell.is_sequential:
            if pin == cell.clock_pin:
                # CP -> Q  ：触发器的翻转时间 Tco
                for q in cell.output_pins:
                    out.append(TimingArc(node, f"{inst_name}/{q}",
                                         design.clk_to_q_delay(inst_name), "clk2q",
                                         cell.name))
            return out
        # 组合逻辑单元：输入引脚 -> 输出引脚
        for a in cell.arcs:
            if a.related_pin == pin:
                out.append(TimingArc(
                    node, f"{inst_name}/{a.output_pin}",
                    design.comb_arc_delay(inst_name, pin, a.output_pin),
                    "comb", cell.name))
        return out

    # 输出引脚 -> 连线上的所有负载
    inst = design._inst_of(inst_name)
    net = m.resolve(inst.conns.get(pin, ""))
    if not net:
        return out
    for sink_inst, sink_pin in design._net_sinks.get(net, []):
        if sink_inst == "@port":
            out.append(TimingArc(node, f"@port:{net}",
                                 design.net_delay(net), "net"))
        else:
            out.append(TimingArc(node, f"{sink_inst}/{sink_pin}",
                                 design.net_delay(net), "net"))
    return out


def _startpoints(design: Design) -> list[tuple[str, str]]:
    """返回 (startpoint, 类型) 。"""
    m = design.module
    sdc = design.sdc
    clock_ports = {c.source for c in sdc.clocks.values() if c.source}
    out: list[tuple[str, str]] = []
    for name, direction in m.ports.items():
        if direction == "input" and name not in clock_ports:
            out.append((f"@port:{name}", "port"))
    for inst in m.instances:
        cell = design.lib.cell(inst.cell)
        if cell.is_sequential and cell.clock_pin:
            out.append((f"{inst.name}/{cell.clock_pin}", "ff"))
    return out


def _is_endpoint(design: Design, node: str) -> bool:
    if node.startswith("@port:"):
        return True
    return design.is_ff_data_pin(node)


def _enumerate(design: Design, start: str, limit_paths: int = 20000) -> list[TimingPath]:
    """从 start 出发 DFS 枚举所有到 endpoint 的路径。"""
    results: list[TimingPath] = []
    counter = [0]

    def dfs(node: str, nodes: list[str], arcs: list[TimingArc], seen: set[str]) -> None:
        if counter[0] > limit_paths:
            return
        if _is_endpoint(design, node) and arcs:
            counter[0] += 1
            results.append(TimingPath(
                startpoint=start, endpoint=node,
                kind="", nodes=list(nodes), arcs=list(arcs)))
            return
        for arc in _out_nodes(design, node):
            if arc.dst in seen:
                continue          # 组合环，跳过
            seen.add(arc.dst)
            nodes.append(arc.dst)
            arcs.append(arc)
            dfs(arc.dst, nodes, arcs, seen)
            arcs.pop()
            nodes.pop()
            seen.discard(arc.dst)

    dfs(start, [start], [], {start})
    return results


# ==========================================================================
# 例外 / 多周期 匹配
# ==========================================================================


def _wild_match(pattern: str, name: str) -> bool:
    if "*" not in pattern:
        return pattern == name
    import re
    return re.fullmatch(re.escape(pattern).replace(r"\*", ".*"), name) is not None


def _ref_matches(ref, path: TimingPath, nodes: set[str], role: str) -> bool:
    """判断一个 SDC 对象是否匹配这条路径。

    ``role`` 决定看哪一端：
        from    -> 只看发送时钟 / 起点
        to      -> 只看捕获时钟 / 终点
        through -> 路径上任意一点
    """
    kind, name = ref.kind, ref.name

    if kind == "clock":
        if role == "from":
            return bool(path.launch_clock) and _wild_match(name, path.launch_clock)
        if role == "to":
            return bool(path.capture_clock) and _wild_match(name, path.capture_clock)
        return ((bool(path.launch_clock) and _wild_match(name, path.launch_clock)) or
                (bool(path.capture_clock) and _wild_match(name, path.capture_clock)))

    sp = _port_of(path.startpoint)
    ep = _port_of(path.endpoint)

    if kind == "port":
        if role == "from":
            return path.startpoint.startswith("@port:") and _wild_match(name, sp)
        if role == "to":
            return path.endpoint.startswith("@port:") and _wild_match(name, ep)
        return _wild_match(name, sp) or _wild_match(name, ep)

    if kind == "pin":
        if role == "from":
            cands = {path.startpoint}
        elif role == "to":
            cands = {path.endpoint}
        else:
            cands = nodes
        # 兼容 DC 常见的 CP/CK 引脚命名差异
        for alt in (name, name.replace("/CP", "/CK"), name.replace("/CK", "/CP")):
            if any(_wild_match(alt, c) for c in cands):
                return True
        return False

    return False


def _filter_matches(filt: PathFilter, path: TimingPath, nodes: set[str]) -> bool:
    if filt.from_ and not any(_ref_matches(r, path, nodes, "from") for r in filt.from_):
        return False
    if filt.to and not any(_ref_matches(r, path, nodes, "to") for r in filt.to):
        return False
    for r in filt.through:
        if not _ref_matches(r, path, nodes, "through"):
            return False
    return True


def _find_multicycle(sdc: SdcDatabase, path: TimingPath, nodes: set[str],
                     kind: str) -> int | None:
    """找出匹配的多周期数。后面的命令覆盖前面的。"""
    found = None
    for mc in sdc.multicycles:
        if mc.kind != kind:
            continue
        if _filter_matches(mc.filt, path, nodes):
            found = mc.cycles
    return found


def _clock_groups_disable(sdc: SdcDatabase, path: TimingPath,
                          nodes: set[str]) -> str | None:
    for cg in sdc.clock_groups:
        hit = []
        for gi, grp in enumerate(cg.groups):
            for cname in grp:
                if path.launch_clock and _wild_match(cname, path.launch_clock):
                    hit.append((gi, "launch"))
                if path.capture_clock and _wild_match(cname, path.capture_clock):
                    hit.append((gi, "capture"))
        idx = {g for g, _ in hit}
        if len(idx) > 1:
            return ("set_clock_groups -asynchronous"
                    if cg.asynchronous else "set_clock_groups")
    return None


# ==========================================================================
# 主分析
# ==========================================================================


@dataclass
class ConstraintVariant:
    """一条路径可能有多种"约束组合"。

    同一个端口上挂多条 I/O 约束时（例如两种不同频率的接收时钟），
    STA 必须**分别**算一遍再取最严格的那个，而不能只看哪个数值最大。
    """

    launch_clock: str | None
    launch_cost: Fraction
    launch_cost_name: str
    capture_clock: str | None
    out_delay: Fraction
    reg_endpoint: bool
    source: str = ""


def _classify(design: Design, path: TimingPath) -> None:
    """判定路径属于四类中的哪一类，并算出起点代价(Tco / input_delay)与组合延时。"""
    sp_port = path.startpoint.startswith("@port:")
    ep_port = path.endpoint.startswith("@port:")
    if sp_port and not ep_port:
        path.kind = "input->reg"
    elif not sp_port and not ep_port:
        path.kind = "reg->reg"
    elif not sp_port and ep_port:
        path.kind = "reg->output"
    else:
        path.kind = "input->output"

    total = sum((a.delay for a in path.arcs), ZERO)
    if path.kind.startswith("reg"):
        # 起点是寄存器的 CP 引脚，路径的第一段就是 CP->Q 的翻转时间 Tco
        launch_inst = path.startpoint.split("/", 1)[0]
        path.launch_cost = design.clk_to_q_delay(launch_inst)
        path.launch_cost_name = "Tco"
        path.comb_delay = total - path.launch_cost
    else:
        path.launch_cost = ZERO
        path.launch_cost_name = ""
        path.comb_delay = total

    # 给时序例外匹配用的"代表时钟"
    variants = _constraint_variants(design, path)
    primary = variants[0] if variants else None
    path.launch_clock = primary.launch_clock if primary else None
    path.capture_clock = primary.capture_clock if primary else None
    if path.kind.startswith("reg"):
        path.launch_clock = design.clock_of_ff_pin(path.startpoint)


def _constraint_variants(design: Design, path: TimingPath) -> list[ConstraintVariant]:
    sdc = design.sdc
    out: list[ConstraintVariant] = []

    if path.kind == "reg->reg":
        ff_clk = design.clock_of_ff_pin(path.startpoint)
        out.append(ConstraintVariant(ff_clk, path.launch_cost, "Tco",
                                     design.clock_of_ff_pin(path.endpoint),
                                     ZERO, True))

    elif path.kind == "reg->output":
        ff_clk = design.clock_of_ff_pin(path.startpoint)
        port = _port_of(path.endpoint)
        specs = [s for s in sdc.output_delays.get(port, []) if s.value_max is not None]
        if not specs:
            out.append(ConstraintVariant(ff_clk, path.launch_cost, "Tco", ff_clk,
                                         ZERO, False))
        for s in specs:
            out.append(ConstraintVariant(ff_clk, path.launch_cost, "Tco",
                                         s.clock, s.value_max, False, s.history))

    elif path.kind == "input->reg":
        cc = design.clock_of_ff_pin(path.endpoint)
        port = _port_of(path.startpoint)
        specs = [s for s in sdc.input_delays.get(port, []) if s.value_max is not None]
        if not specs:
            out.append(ConstraintVariant(None, ZERO, "", cc, ZERO, True))
        for s in specs:
            out.append(ConstraintVariant(s.clock, s.value_max, "input_delay",
                                         cc, ZERO, True, s.history))

    else:  # input->output
        iport = _port_of(path.startpoint)
        oport = _port_of(path.endpoint)
        ispecs = [s for s in sdc.input_delays.get(iport, []) if s.value_max is not None]
        ospecs = [s for s in sdc.output_delays.get(oport, []) if s.value_max is not None]
        if not ispecs and not ospecs:
            out.append(ConstraintVariant(None, ZERO, "", None, ZERO, False))
        for i in (ispecs or [None]):
            for o in (ospecs or [None]):
                out.append(ConstraintVariant(
                    i.clock if i else None,
                    i.value_max if i else ZERO,
                    "input_delay" if i else "",
                    o.clock if o else None,
                    o.value_max if o else ZERO,
                    False))
    return out


@dataclass
class _Best:
    """某条路径在一个约束组合下的最差时序结果。"""

    slack: Fraction
    variant: ConstraintVariant | None
    t_launch: Fraction
    t_capture: Fraction | None
    req: Fraction
    arr: Fraction
    arr_base: Fraction
    from_max_delay: bool = False


def analyze(design: Design, verbose: bool = False) -> AnalysisResult:
    sdc = design.sdc
    res = AnalysisResult(design_name=design.top)
    res.log = list(sdc.log)
    res.warnings = list(sdc.warnings)

    clocks = {c.name: ClockEdges(c) for c in sdc.clocks.values()}
    if clocks:
        base = Fraction(1)
        for c in sdc.clocks.values():
            base = lcm_frac(base, c.period)
    else:
        base = Fraction(1)

    # ---- 枚举路径 -------------------------------------------------------
    raw_paths: list[TimingPath] = []
    for sp, _typ in _startpoints(design):
        raw_paths.extend(_enumerate(design, sp))

    for path in raw_paths:
        nodes = path.node_set()
        _classify(design, path)

        # false path
        for exc in sdc.exceptions:
            if exc.kind != "false_path":
                continue
            if _filter_matches(exc.filt, path, nodes):
                reason = f"set_false_path {_fmt_filter(exc.filt)}"
                if exc.setup:
                    path.setup_disabled = path.setup_disabled or reason
                if exc.hold:
                    path.hold_disabled = path.hold_disabled or reason

        # clock group
        cg = _clock_groups_disable(sdc, path, nodes)
        if cg:
            path.setup_disabled = path.setup_disabled or cg
            path.hold_disabled = path.hold_disabled or cg

        # max_delay
        for exc in sdc.exceptions:
            if exc.kind == "max_delay" and exc.value is not None:
                if _filter_matches(exc.filt, path, nodes):
                    path.max_delay_limit = exc.value

    # ---- 逐条算 slack ---------------------------------------------------
    for path in raw_paths:
        nodes = path.node_set()
        pt = PathTiming(path=path)
        res.paths.append(pt)

        variants = _constraint_variants(design, path)

        # 终点触发器的建立/保持要求（来自工艺库）
        t_setup = ZERO
        t_hold = ZERO
        if path.kind in ("reg->reg", "input->reg"):
            ep_cell = design._cell_of(path.endpoint.split("/", 1)[0])
            t_setup, t_hold = ep_cell.setup, ep_cell.hold

        m_setup = _find_multicycle(sdc, path, nodes, "setup")
        m_hold = _find_multicycle(sdc, path, nodes, "hold")
        m_setup = 1 if m_setup is None else m_setup
        m_hold = 0 if m_hold is None else m_hold
        pt.m_setup, pt.m_hold = m_setup, m_hold

        best_setup: _Best | None = None
        best_hold: _Best | None = None

        # 对每个约束组合分别算，取最差
        for v in variants:
            lc = clocks.get(v.launch_clock) if v.launch_clock else None
            cc = clocks.get(v.capture_clock) if v.capture_clock else None
            if lc is None or cc is None:
                continue
            launch_spec = sdc.clocks[v.launch_clock]
            capture_spec = sdc.clocks[v.capture_clock]
            launch_lat = launch_spec.latency_source_max + launch_spec.latency_network_max
            capture_lat = (capture_spec.latency_source_max
                           + capture_spec.latency_network_max)

            # 遍历一个公共基本周期内的所有发送沿
            k = 0
            while lc.rise(k) < base:
                t_L = lc.rise(k)
                i0 = first_rise_index_after(cc, t_L)
                setup_idx = i0 + (m_setup - 1)        # 建立沿
                hold_idx = setup_idx - 1 - m_hold     # 保持沿
                t_su = cc.rise(setup_idx)
                t_hd = cc.rise(hold_idx)

                arr_base = t_L + launch_lat + v.launch_cost
                arr = arr_base + path.comb_delay

                # ---- 建立时间：T_required ----
                if v.reg_endpoint:
                    req = t_su + capture_lat - t_setup - capture_spec.unc_setup
                else:
                    req = t_su + capture_lat - v.out_delay - capture_spec.unc_setup
                slack = req - arr
                if best_setup is None or slack < best_setup.slack:
                    best_setup = _Best(slack, v, t_L, t_su, req, arr, arr_base)

                # ---- 保持时间：T_required = 保持沿 + Th + uncertainty ----
                if v.reg_endpoint:
                    req_h = t_hd + capture_lat + t_hold + capture_spec.unc_hold
                    sh = arr - req_h
                    if best_hold is None or sh < best_hold.slack:
                        best_hold = _Best(sh, v, t_L, t_hd, req_h, arr, arr_base)

                k += 1
                if k > 4096:
                    break

        # set_max_delay：直接限制"起点 -> 终点"的路径延时
        if path.max_delay_limit is not None:
            path_delay = path.comb_delay + (path.launch_cost
                                            if path.kind.startswith("reg") else ZERO)
            slack_md = path.max_delay_limit - path_delay
            if best_setup is None or slack_md < best_setup.slack:
                best_setup = _Best(slack_md, None, ZERO, None,
                                   path.max_delay_limit, path_delay, ZERO, True)

        if best_setup is not None:
            pt.launch_edge = best_setup.t_launch
            pt.capture_edge_setup = best_setup.t_capture
            pt.req_setup = best_setup.req
            pt.arr = best_setup.arr
            pt.arr_base = best_setup.arr_base
            pt.slack_setup = best_setup.slack
            if best_setup.variant is not None:
                path.launch_clock = best_setup.variant.launch_clock
                path.capture_clock = best_setup.variant.capture_clock
        if best_hold is not None:
            pt.capture_edge_hold = best_hold.t_capture
            pt.req_hold = best_hold.req
            pt.slack_hold = best_hold.slack

        # ---- 被时序例外屏蔽 / 未被约束的路径不报 slack -------------------
        if path.setup_disabled:
            pt.note = path.setup_disabled
            pt.slack_setup = None
            pt.slack_hold = None
        elif path.kind.startswith("input") and not sdc.input_delays.get(
                _port_of(path.startpoint)):
            pt.note = "输入端口没有 set_input_delay，路径未约束"
            pt.slack_setup = None
            pt.slack_hold = None
        elif path.kind.endswith("->output") and path.max_delay_limit is None \
                and not sdc.output_delays.get(_port_of(path.endpoint)):
            pt.note = "输出端口没有 set_output_delay，路径未约束"
            pt.slack_setup = None
            pt.slack_hold = None

    return res


def _port_of(node: str) -> str:
    return node.split(":", 1)[1] if node.startswith("@port:") else node


def _fmt_filter(filt: PathFilter) -> str:
    parts = []
    if filt.from_:
        parts.append("from=" + ",".join(r.name for r in filt.from_))
    if filt.through:
        parts.append("through=" + ",".join(r.name for r in filt.through))
    if filt.to:
        parts.append("to=" + ",".join(r.name for r in filt.to))
    return " ".join(parts)
