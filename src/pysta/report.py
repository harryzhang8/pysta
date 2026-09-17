# -*- coding: utf-8 -*-
"""
时序报告输出
============

尽量模仿 Design Compiler / PrimeTime 的 ``report_timing`` 排版，
这样熟悉商业工具的人一看就知道每一行对应哪个概念::

    Startpoint: FF1 (rising edge-triggered flip-flop clocked by clk)
    Endpoint  : FF2 (rising edge-triggered flip-flop clocked by clk)
    Path Group: clk
    Path Type : max

      Point                                    Incr       Path
      ------------------------------------------------------------
      clock clk (rise edge)                  0.0000     0.0000
      clock network delay (ideal)            0.0000     0.0000
      FF1/CP (DFFRQ)                         0.0000     0.0000
      FF1/Q (DFFRQ)                          1.0000     1.0000    <- Tco
      U1/Y (INVX1)                           0.0600     1.0600
      U2/Y (NAND2X1)                         0.0750     1.1350
      FF2/D (DFFRQ)                          0.0000     1.1350
      data arrival time                                1.1350
      ...
"""

from __future__ import annotations

from fractions import Fraction

from .timing import AnalysisResult, Design, PathTiming, TimingPath, ZERO

__all__ = ["format_path", "format_summary", "format_design", "format_all"]

_LINE = "  " + "-" * 74


def n(v: Fraction | float | None, width: int = 8, prec: int = 4) -> str:
    """把精确有理数格式化成定宽字符串。"""
    if v is None:
        return "-".rjust(width)
    return f"{float(v):.{prec}f}".rjust(width)


def _ff_desc(design: Design, pin_node: str) -> str:
    """``FF1/CP`` -> 'rising edge-triggered flip-flop clocked by clk'"""
    inst = pin_node.split("/", 1)[0]
    clk = design.clock_of_instance(inst)
    if clk is None:
        return "sequential cell"
    return f"rising edge-triggered flip-flop clocked by {clk}"


def format_path(design: Design, pt: PathTiming, title: str = "") -> str:
    """输出单条路径的详细时序报告。"""
    sdc = design.sdc
    p = pt.path
    L: list[str] = []

    if title:
        L.append(f"===== {title} =====")
    L.append(f"Startpoint: {p.startpoint.lstrip('@port:').replace('@port:', '')}"
             + (f"  ({_ff_desc(design, p.startpoint)})"
                if not p.startpoint.startswith("@port:") else "  (input port)"))
    L.append(f"Endpoint  : {p.endpoint.replace('@port:', '')}"
             + (f"  ({_ff_desc(design, p.endpoint)})"
                if not p.endpoint.startswith("@port:") else "  (output port)"))
    L.append(f"Path Group: {p.launch_clock or '-'}"
             f"{' -> ' + p.capture_clock if p.capture_clock != p.launch_clock else ''}")
    L.append(f"Path Type : max     (多周期: setup={pt.m_setup}  hold={pt.m_hold})")
    L.append(f"Path Kind : {p.kind}")

    if p.setup_disabled:
        L.append(f"!! 该路径已被时序例外屏蔽: {p.setup_disabled}")
        L.append("   （不再做建立时间分析，也不参与优化）")
        L.append("")
        return "\n".join(L)

    if pt.slack_setup is None:
        L.append(f"!! 该路径未被约束: {pt.note}")
        L.append("")
        return "\n".join(L)

    L.append("")
    L.append(f"  {'Point'.ljust(38)}{'Incr'.rjust(10)}{'Path'.rjust(12)}")
    L.append(_LINE)

    launch_spec = sdc.clocks.get(p.launch_clock)
    t = ZERO

    def emit(name: str, incr: Fraction | None, comment: str = "") -> None:
        nonlocal t
        if incr is not None:
            t = t + incr
        tag = f"    <- {comment}" if comment else ""
        L.append(f"  {name.ljust(38)}{n(incr, 10)}{n(t, 12)}{tag}")

    emit(f"clock {p.launch_clock} (rise edge)", pt.launch_edge)
    if launch_spec is not None and launch_spec.latency_source_max:
        emit("clock source latency (source)", launch_spec.latency_source_max)
    if launch_spec is not None and launch_spec.latency_network_max:
        emit("clock network delay (network)", launch_spec.latency_network_max)

    if not p.startpoint.startswith("@port:"):
        inst = p.startpoint.split("/", 1)[0]
        emit(f"{p.startpoint} ({design._cell_of(inst).name})", None)
    else:
        emit(f"{p.startpoint.replace('@port:', '')} (input port)", None)
        emit("input delay", p.launch_cost, "set_input_delay")

    for arc in p.arcs:
        cell = f" ({arc.cell})" if arc.cell else ""
        cmt = ""
        if arc.kind == "clk2q":
            cmt = "Tco  CP->Q 翻转时间"
        elif arc.kind == "net" and arc.delay:
            cmt = "线网延时"
        elif arc.kind == "comb":
            cmt = arc.cell
        emit(f"{arc.dst.replace('@port:', '')}{cell}", arc.delay, cmt)

    L.append(f"  {'data arrival time'.ljust(38)}{'':>10}{n(pt.arr, 12)}")
    L.append("")

    capture_edge = pt.capture_edge_setup
    capture_spec = sdc.clocks.get(p.capture_clock) if p.capture_clock else None
    tt = ZERO

    def emit2(name: str, delta: Fraction | None, comment: str = "") -> None:
        nonlocal tt
        if delta is not None:
            tt = tt + delta
        tag = f"    <- {comment}" if comment else ""
        L.append(f"  {name.ljust(38)}{n(delta, 10)}{n(tt, 12)}{tag}")

    emit2(f"clock {p.capture_clock} (rise edge)", capture_edge,
          f"第 {pt.m_setup} 个捕获沿")
    if capture_spec is not None and capture_spec.latency_source_max:
        emit2("clock source latency (source)", capture_spec.latency_source_max)
    if capture_spec is not None and capture_spec.latency_network_max:
        emit2("clock network delay (network)", capture_spec.latency_network_max)

    if p.kind in ("reg->reg", "input->reg"):
        ep_inst = p.endpoint.split("/", 1)[0]
        cell = design._cell_of(ep_inst)
        if capture_spec is not None and capture_spec.unc_setup:
            emit2("clock uncertainty", -capture_spec.unc_setup, "set_clock_uncertainty")
        emit2(f"{p.endpoint} ({cell.name})", None)
        emit2("library setup time", -cell.setup, f"Tsu = {float(cell.setup):g}ns")
    else:
        port = p.endpoint.replace("@port:", "")
        od = ZERO
        for s in sdc.output_delays.get(port, []):
            if s.value_max is not None and s.clock == p.capture_clock:
                od = s.value_max
        emit2("output delay", -od, "set_output_delay")

    L.append(f"  {'data required time'.ljust(38)}{'':>10}{n(pt.req_setup, 12)}")
    L.append(_LINE)
    L.append(f"  {'data required time'.ljust(38)}{'':>10}{n(pt.req_setup, 12)}")
    L.append(f"  {'data arrival time'.ljust(38)}{'':>10}{n(-pt.arr, 12)}")
    L.append(_LINE)
    verdict = "MET" if pt.slack_setup >= 0 else "VIOLATED"
    L.append(f"  {f'slack ({verdict})'.ljust(38)}{'':>10}{n(pt.slack_setup, 12)}")

    budget = pt.budget_setup
    L.append("")
    L.append(f"  >>> 组合逻辑时序预算(budget) = {float(budget):.4f}ns"
             f"   实际组合延时 = {float(p.comb_delay):.4f}ns")

    # 保持时间
    if pt.slack_hold is not None:
        L.append("")
        L.append(f"  --- 保持时间 (hold) ---")
        L.append(f"  保持检查沿 = {float(pt.capture_edge_hold):.4f}ns"
                 f"   数据到达 = {float(pt.arr):.4f}ns"
                 f"   需求 = {float(pt.req_hold):.4f}ns")
        vh = "MET" if pt.slack_hold >= 0 else "VIOLATED"
        L.append(f"  {'slack hold (' + vh + ')'.ljust(38)}{'':>10}{n(pt.slack_hold, 12)}")

    L.append("")
    return "\n".join(L)


def format_summary(res: AnalysisResult, show_unconstrained: bool = False) -> str:
    """按 slack 排序的汇总表。"""
    L: list[str] = []
    L.append(f"  {'Type'.ljust(14)}{'Startpoint'.ljust(15)}{'Endpoint'.ljust(15)}"
             f"{'Comb'.rjust(9)}{'Budget'.rjust(10)}{'Slack'.rjust(10)}"
             f"{'Hold'.rjust(10)}  Verdict")
    L.append(_LINE)

    rows = [p for p in res.paths if p.slack_setup is not None or p.path.setup_disabled]
    if not show_unconstrained:
        rows = [p for p in rows if p.slack_setup is not None or p.path.setup_disabled]
    rows = sorted(rows, key=lambda x: (x.slack_setup is None, 
                                       x.slack_setup if x.slack_setup is not None else ZERO))

    for pt in rows:
        p = pt.path
        verdict = pt.verdict
        mark = {"MET": "  MET", "VIOLATED": "! VIOLATED", "EXCLUDED": "x EXCLUDED",
                "UNCONSTRAINED": "? UNCONSTRAINED"}[verdict]
        L.append(f"  {p.kind.ljust(14)}"
                 f"{p.startpoint.replace('@port:', '').ljust(15)}"
                 f"{p.endpoint.replace('@port:', '').ljust(15)}"
                 f"{n(pt.comb_delay, 9)}{n(pt.budget_setup, 10)}"
                 f"{n(pt.slack_setup, 10)}{n(pt.slack_hold, 10)}  {mark}")

    others = [p for p in res.paths if p.slack_setup is None and not p.path.setup_disabled]
    if others and show_unconstrained:
        L.append("")
        L.append("  未约束 / 被屏蔽的路径：")
        for pt in others:
            L.append(f"    {pt.path.kind.ljust(14)}"
                     f"{pt.path.startpoint.replace('@port:', '').ljust(15)}"
                     f"{pt.path.endpoint.replace('@port:', '').ljust(15)}"
                     f"{pt.note}")
    return "\n".join(L)


def format_design(design: Design, res: AnalysisResult, verbose_paths: int = 3) -> str:
    """一个例子的完整报告：SDC 回显 + 汇总 + 最差路径详情。"""
    sdc = design.sdc
    L: list[str] = []
    bar = "=" * 78
    L.append(bar)
    L.append(f"设计: {design.top}    网表: {design.netlist_path.name}"
             f"    SDC: {design.sdc_path.name if design.sdc_path else '-'}")
    L.append(bar)

    L.append("")
    L.append("[ 时钟 ]")
    for c in sdc.clocks.values():
        kind = "虚拟时钟" if c.is_virtual else f"源端口 {c.source}"
        extra = []
        if c.unc_setup:
            extra.append(f"uncertainty(setup)={float(c.unc_setup):g}")
        if c.latency_source_max:
            extra.append(f"source_latency={float(c.latency_source_max):g}")
        if c.latency_network_max:
            extra.append(f"network_latency={float(c.latency_network_max):g}")
        if c.transition_max:
            extra.append(f"transition_max={float(c.transition_max):g}")
        if c.propagated:
            extra.append("propagated")
        tail = ("   [" + ", ".join(extra) + "]") if extra else ""
        L.append(f"  {c.name.ljust(10)} period = {float(c.period):<10.4f} ns"
                 f"  waveform = [{', '.join(f'{float(w):g}' for w in c.waveform)}]"
                 f"  ({kind}){tail}")

    if sdc.input_delays:
        L.append("")
        L.append("[ 输入端口约束 ]")
        for port, specs in sdc.input_delays.items():
            for s in specs:
                L.append(f"  {port.ljust(10)} max = {float(s.value_max or 0):<8.3f} ns"
                         f"  -clock {s.clock}"
                         f"{'  -add_delay' if s.add_delay else ''}")
    if sdc.output_delays:
        L.append("")
        L.append("[ 输出端口约束 ]")
        for port, specs in sdc.output_delays.items():
            for s in specs:
                L.append(f"  {port.ljust(10)} max = {float(s.value_max or 0):<8.3f} ns"
                         f"  -clock {s.clock}"
                         f"{'  -add_delay' if s.add_delay else ''}")

    if sdc.multicycles:
        L.append("")
        L.append("[ 多周期路径 ]")
        for mc in sdc.multicycles:
            L.append(f"  -{mc.kind} {mc.cycles}"
                     f"   from={[r.name for r in mc.filt.from_] or '-'}"
                     f"  through={[r.name for r in mc.filt.through] or '-'}"
                     f"  to={[r.name for r in mc.filt.to] or '-'}")

    if sdc.exceptions:
        L.append("")
        L.append("[ 时序例外 ]")
        for exc in sdc.exceptions:
            L.append(f"  {exc.kind}"
                     f"   from={[r.name for r in exc.filt.from_] or '-'}"
                     f"  through={[r.name for r in exc.filt.through] or '-'}"
                     f"  to={[r.name for r in exc.filt.to] or '-'}"
                     + (f"  value={float(exc.value):g}" if exc.value is not None else ""))

    if sdc.warnings:
        L.append("")
        L.append("[ SDC 警告 ]")
        for w in sdc.warnings:
            L.append(f"  ! {w}")

    L.append("")
    L.append("[ 时序汇总 ]")
    L.append(format_summary(res))
    L.append("")

    constrained = sorted(res.constrained(), key=lambda x: x.slack_setup)
    if constrained:
        worst = constrained[:verbose_paths]
        heading = "最差路径详情"
    else:
        # 全部路径都被时序例外屏蔽时，也要把屏蔽原因打出来，别让报告空着
        worst = [p for p in res.paths if p.path.setup_disabled][:verbose_paths]
        heading = "路径被时序例外屏蔽"

    if worst:
        L.append(f"[ {heading} ]")
        for i, pt in enumerate(worst, 1):
            L.append("")
            L.append(format_path(design, pt, title=f"路径 #{i}"))
    return "\n".join(L)


def format_all(reports: list[tuple[Design, AnalysisResult]]) -> str:
    """所有例子的总报告。"""
    parts: list[str] = []
    parts.append("=" * 78)
    parts.append("pysta 时序分析报告")
    parts.append("=" * 78)
    for design, res in reports:
        parts.append("")
        parts.append(format_design(design, res))
    return "\n".join(parts)
