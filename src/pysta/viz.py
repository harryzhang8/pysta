# -*- coding: utf-8 -*-
"""
时序可视化（自包含 HTML + SVG）
==============================

生成一个 **单文件 HTML**，双击就能用浏览器打开，里面每个例子一张卡片：

    左边：SDC 约束回显 + 关键数字（预算 / 实际延时 / slack）
    右边：时序波形图 —— 发送沿、建立捕获沿、保持捕获沿、数据到达时刻
          以及"setup 窗口"和"hold 窗口"的直观位置

不依赖 matplotlib / plotly，纯手写 SVG，所以没有任何第三方依赖，
也方便直接内嵌到文档里。
"""

from __future__ import annotations

import html
import re
from fractions import Fraction

from .timing import AnalysisResult, Design, PathTiming, ZERO

__all__ = ["render_html", "write_html"]


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def fx(v: Fraction | float | None, prec: int = 3) -> str:
    if v is None:
        return "-"
    return f"{float(v):.{prec}f}"


def _nice(v: float) -> float:
    """把数字修约成好看的刻度值。"""
    if v <= 0:
        return 1.0
    mag = 10 ** int(f"{v:e}".split("e")[1])
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


# --------------------------------------------------------------------------
# SVG 时序波形
# --------------------------------------------------------------------------


class _Svg:
    def __init__(self, w: float, h: float) -> None:
        self.w, self.h = w, h
        self.parts: list[str] = []

    def add(self, s: str) -> "_Svg":
        self.parts.append(s)
        return self

    def text(self, x, y, s, size=11, anchor="middle", fill="#334155",
             weight="400", family="ui-monospace, Consolas, monospace") -> "_Svg":
        return self.add(
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}" '
            f'fill="{fill}" font-weight="{weight}" font-family="{family}">{html.escape(s)}</text>')

    def line(self, x1, y1, x2, y2, stroke="#94a3b8", width=1, dash=None,
             cap="butt") -> "_Svg":
        d = f' stroke-dasharray="{dash}"' if dash else ""
        return self.add(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                        f'stroke="{stroke}" stroke-width="{width}" stroke-linecap="{cap}"{d}/>')

    def rect(self, x, y, w, h, fill="#e2e8f0", stroke="none", rx=3, opacity=1.0) -> "_Svg":
        return self.add(f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(w, 0):.2f}" '
                        f'height="{max(h, 0):.2f}" rx="{rx}" fill="{fill}" '
                        f'stroke="{stroke}" opacity="{opacity}"/>')

    def poly(self, pts, stroke="#2563eb", width=1.8, fill="none") -> "_Svg":
        p = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
        return self.add(f'<polyline points="{p}" fill="{fill}" stroke="{stroke}" '
                        f'stroke-width="{width}" stroke-linejoin="round"/>')

    def path(self, d, stroke="#94a3b8", width=1, fill="none", dash=None) -> "_Svg":
        dd = f' stroke-dasharray="{dash}"' if dash else ""
        return self.add(f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
                        f'stroke-width="{width}"{dd}/>')

    def render(self, extra_class: str = "") -> str:
        return (f'<svg class="wave {extra_class}" viewBox="0 0 {self.w:.0f} {self.h:.0f}" '
                f'width="100%" preserveAspectRatio="xMidYMid meet" '
                f'xmlns="http://www.w3.org/2000/svg">' + "".join(self.parts) + "</svg>")


def _clock_polyline(clock, x_of, t0: float, t1: float, y_high: float, y_low: float,
                    color: str) -> list[tuple[float, float]]:
    """把一个时钟画成方波折线。"""
    period = float(clock.period)
    r0 = float(clock.rise_time())
    f0 = float(clock.fall_time())
    if period <= 0:
        return []
    pts: list[tuple[float, float]] = [(x_of(t0), y_low)]
    k = int((t0 - r0) // period) - 1
    while True:
        r = r0 + k * period
        f = f0 + k * period
        if r > t1:
            break
        if f > t0:
            pts.append((x_of(max(r, t0)), y_low))
            pts.append((x_of(max(r, t0)), y_high))
            pts.append((x_of(min(f, t1)), y_high))
            pts.append((x_of(min(f, t1)), y_low))
        k += 1
        if k > 500:
            break
    pts.append((x_of(t1), y_low))
    return pts


def timing_waveform(design: Design, pt: PathTiming, width: float = 620.0) -> str:
    """单条路径的时序波形图。"""
    p = pt.path
    sdc = design.sdc

    if p.setup_disabled:
        s = _Svg(width, 60)
        s.rect(8, 14, width - 16, 34, "#f1f5f9")
        s.text(width / 2, 36, f"该路径被时序例外屏蔽：{p.setup_disabled}",
               size=12, fill="#64748b")
        return s.render()

    if pt.slack_setup is None:
        s = _Svg(width, 60)
        s.rect(8, 14, width - 16, 34, "#f1f5f9")
        s.text(width / 2, 36, pt.note or "路径未被约束", size=12, fill="#64748b")
        return s.render()

    launch = sdc.clocks.get(p.launch_clock)
    capture = sdc.clocks.get(p.capture_clock)
    arr = float(pt.arr)
    req = float(pt.req_setup)
    t_launch = float(pt.launch_edge)
    t_su = float(pt.capture_edge_setup)
    t_hd = float(pt.capture_edge_hold) if pt.capture_edge_hold is not None else None

    lo = min(0.0, t_hd if t_hd is not None else 0.0, t_launch)
    hi = max(t_su, arr, req) * 1.18 + 1e-9
    hi = max(hi, t_su + (float(capture.period) if capture else 0) * 0.35)

    width = float(width)
    L, R = 54.0, width - 14.0

    def x_of(t: float) -> float:
        return L + (t - lo) / (hi - lo) * (R - L)

    same_clock = (launch is not None and capture is not None
                  and launch.name == capture.name)
    rows = 1 if same_clock else 2

    # 版面：标签区 -> 时钟行 -> 数据条 -> slack -> 时间轴
    lbl_y = 16.0
    row_top = 52.0
    row_h = 42.0
    bar_y = row_top + rows * row_h + 30.0
    slack_y = bar_y + 44.0
    axis_y = slack_y + 28.0
    hold_bad = pt.slack_hold is not None and pt.slack_hold < 0
    H = axis_y + 30.0 + (34.0 if hold_bad else 0.0)

    s = _Svg(width, H)

    # ---- 时钟波形（发送/捕获是同一个时钟时只画一行）----
    if launch is not None:
        y_hi, y_lo = row_top, row_top + 24.0
        s.text(8, y_lo - 6, launch.name, size=11, anchor="start",
               fill="#0369a1", weight="600")
        s.poly(_clock_polyline(launch, x_of, lo, hi, y_hi, y_lo, "#0ea5e9"),
               stroke="#0ea5e9", width=1.8)
        launch_row = (y_hi, y_lo)
    else:
        launch_row = (row_top, row_top + 24.0)

    if same_clock:
        capture_row = launch_row
    elif capture is not None:
        y_hi, y_lo = row_top + row_h, row_top + row_h + 24.0
        s.text(8, y_lo - 6, capture.name, size=11, anchor="start",
               fill="#7c3aed", weight="600")
        s.poly(_clock_polyline(capture, x_of, lo, hi, y_hi, y_lo, "#8b5cf6"),
               stroke="#8b5cf6", width=1.8)
        capture_row = (y_hi, y_lo)
    else:
        capture_row = launch_row

    # ---- 关键沿。标签按需错层摆放，避免同刻度的沿互相压字 ----
    markers: list[tuple[float, str, str, tuple[float, float]]] = []
    if t_hd is not None:
        markers.append((t_hd, f"保持捕获沿 {t_hd:g}ns", "#dc2626", capture_row))
    markers.append((t_launch, f"发送沿 {t_launch:g}ns", "#0284c7", launch_row))
    markers.append((t_su, f"建立捕获沿 {t_su:g}ns", "#7c3aed", capture_row))
    markers.sort(key=lambda m: m[0])

    placed: list[list[float]] = []
    min_gap = 96.0
    for t, label, color, (y_hi, y_lo) in markers:
        x = x_of(t)
        level = 0
        while level < len(placed) and any(abs(x - px) < min_gap for px in placed[level]):
            level += 1
        if level == len(placed):
            placed.append([])
        placed[level].append(x)
        ty = lbl_y + level * 18.0
        s.line(x, ty + 6, x, axis_y, color, 1.3, dash="4 4")
        s.line(x, y_hi - 2, x, y_lo + 2, color, 1.8)
        s.add(f'<circle cx="{x:.2f}" cy="{y_lo + 2:.2f}" r="3.2" fill="{color}"/>')
        s.text(x, ty, label, size=10.5, fill=color, weight="700")

    # ---- 时间轴 ----
    s.line(L, axis_y, R, axis_y, "#cbd5e1", 1)
    step = _nice((hi - lo) / 7)
    tick = 0.0
    while tick <= hi + 1e-9:
        if tick >= lo - 1e-9:
            x = x_of(tick)
            s.line(x, axis_y, x, axis_y + 4, "#cbd5e1", 1)
            s.text(x, axis_y + 16, f"{tick:g}", size=10, fill="#94a3b8")
        tick += step
    s.text(10, bar_y + 4, "数据", size=11, anchor="start", fill="#334155", weight="600")

    # 起点代价（Tco 或 input_delay）
    cost = float(p.launch_cost)
    comb = float(p.comb_delay)
    x0 = x_of(t_launch)
    x_cost = x_of(t_launch + cost)
    x_arr = x_of(arr)
    s.rect(x0, bar_y - 9, x_cost - x0, 18, "#fed7aa", stroke="#fb923c", rx=3)
    s.rect(x_cost, bar_y - 9, max(x_arr - x_cost, 1.2), 18,
           "#bfdbfe" if pt.slack_setup >= 0 else "#fecaca",
           stroke="#60a5fa" if pt.slack_setup >= 0 else "#ef4444", rx=3)
    if x_cost - x0 > 34:
        s.text((x0 + x_cost) / 2, bar_y + 4, f"{p.launch_cost_name or 'Tco'}",
               size=9.5, fill="#9a3412")
    if x_arr - x_cost > 40:
        s.text((x_cost + x_arr) / 2, bar_y + 4, f"组合 {comb:.2f}", size=9.5,
               fill="#1e3a8a")
    s.text(x_arr, bar_y - 14, f"到达 {arr:.3f}", size=10, fill="#1e293b",
           weight="600")

    # 需求时间
    x_req = x_of(req)
    s.line(x_req, bar_y - 14, x_req, bar_y + 14, "#16a34a", 2.0)
    s.text(x_req, bar_y + 27, f"需求 {req:.3f}", size=10, fill="#15803d",
           weight="600")

    # slack 标注
    ok = pt.slack_setup >= 0
    color = "#16a34a" if ok else "#dc2626"
    if abs(x_req - x_arr) > 4:
        y = slack_y
        s.line(x_arr, y, x_req, y, color, 2.2, cap="round")
        s.line(x_arr, y - 4, x_arr, y + 4, color, 1.6)
        s.line(x_req, y - 4, x_req, y + 4, color, 1.6)
        s.text((x_arr + x_req) / 2, y - 8,
               f"slack = {float(pt.slack_setup):+.4f}ns   "
               f"（预算 {float(pt.budget_setup):.4f}）",
               size=11, fill=color, weight="700")
    else:
        s.text(width / 2, slack_y,
               f"slack = {float(pt.slack_setup):+.4f}ns  "
               f"（预算 {float(pt.budget_setup):.4f}）",
               size=11, fill=color, weight="700")

    # 保持时间违例单独拉一条红带，免得被"建立满足"的绿色掩盖
    if hold_bad:
        y0 = axis_y + 14.0
        s.rect(L, y0, R - L, 24.0, "#fef2f2", stroke="#fecaca", rx=5)
        s.text((L + R) / 2, y0 + 16.5,
               f"保持时间违例：在 {float(t_hd):g}ns 这一沿检查，"
               f"要求组合逻辑延时 ≥ {float(pt.budget_hold):.4f}ns，"
               f"实际只有 {float(pt.comb_delay):.4f}ns "
               f"⇒ hold slack = {float(pt.slack_hold):+.4f}ns",
               size=10.5, fill="#b91c1c", weight="700")
    return s.render()


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

_CSS = """
:root{
  --bg:#f6f8fb; --card:#ffffff; --ink:#0f172a; --muted:#64748b;
  --line:#e2e8f0; --accent:#2563eb; --ok:#16a34a; --bad:#dc2626; --warn:#d97706;
}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px 64px;background:var(--bg);color:var(--ink);
  font-family:"Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.02em}
h2{font-size:18px;margin:0}
.sub{color:var(--muted);font-size:13.5px;margin:0 0 26px;line-height:1.7}
.sub code{background:#eef2f7;padding:1px 5px;border-radius:4px;font-size:12.5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:20px 22px;margin-bottom:18px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.card>header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;
  border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:16px}
.tag{font-size:11.5px;font-weight:700;padding:3px 9px;border-radius:999px;
  background:#eef2ff;color:#4338ca;letter-spacing:.02em}
.tag.ok{background:#dcfce7;color:#15803d}
.tag.bad{background:#fee2e2;color:#b91c1c}
.tag.warn{background:#fef3c7;color:#b45309}
.desc{color:var(--muted);font-size:13px;line-height:1.65;flex-basis:100%;margin:0}
.grid{display:grid;grid-template-columns:minmax(300px,1fr) minmax(380px,1.35fr);
  gap:22px;align-items:start}
@media(max-width:860px){.grid{grid-template-columns:1fr}}
pre{margin:0;background:#0f172a;color:#e2e8f0;border-radius:10px;padding:14px 16px;
  font-size:12.5px;line-height:1.7;overflow:auto;font-family:ui-monospace,Consolas,monospace;
  max-height:330px}
pre .c{color:#94a3b8}
pre .k{color:#7dd3fc}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:13px;
  margin:14px 0 0;font-family:ui-monospace,Consolas,monospace}
.kv dt{color:var(--muted);white-space:nowrap}
.kv dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
.kv dd.ok{color:var(--ok);font-weight:700}
.kv dd.bad{color:var(--bad);font-weight:700}
table{width:100%;border-collapse:collapse;font-size:13px;
  font-variant-numeric:tabular-nums}
th,td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--line);
  white-space:nowrap}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;
  letter-spacing:.03em}
tr:last-child td{border-bottom:none}
td.ok{color:var(--ok);font-weight:700}
td.bad{color:var(--bad);font-weight:700}
td.mute{color:var(--muted)}
.mono{font-family:ui-monospace,Consolas,monospace}
svg.wave{display:block;width:100%;height:auto}
.wavewrap{margin-top:18px;background:#fbfdff;border:1px solid var(--line);
  border-radius:10px;padding:10px 14px 4px}
.note{font-size:12.5px;color:var(--muted);line-height:1.7;margin-top:10px}
.bar{height:9px;border-radius:5px;background:#e2e8f0;position:relative;
  margin-top:6px;overflow:hidden}
.bar>i{position:absolute;left:0;top:0;bottom:0;border-radius:5px}
details{margin-top:14px}
summary{cursor:pointer;font-size:13px;color:var(--accent);font-weight:600;
  user-select:none}
details pre{margin-top:10px}
"""


def _sdc_html(design: Design) -> str:
    """把 SDC 源文件渲染成带简单高亮的 HTML。"""
    if design.sdc_path is None:
        return ""
    raw = design.sdc_path.read_text(encoding="utf-8")
    lines = []
    for ln in raw.splitlines():
        esc = html.escape(ln)
        if ln.strip().startswith("#"):
            lines.append(f'<span class="c">{esc}</span>')
        else:
            esc = re.sub(r"(?<![\w-])(-[A-Za-z_][\w]*)", r'<span class="k">\1</span>', esc)
            lines.append(esc)
    return "\n".join(lines)


def _card(design: Design, res: AnalysisResult, meta: dict, anchor: str) -> str:
    """生成一个示例的卡片。"""
    sdc = design.sdc

    # 选一条"代表性路径"：优先展现违例，其次 slack 最小的已约束路径
    cons = sorted(res.constrained(), key=lambda x: x.slack_setup)
    viol = res.violations()
    hviol = res.hold_violations()
    if viol:
        rep = sorted(viol, key=lambda x: x.slack_setup)[0]
    elif hviol:
        rep = sorted(hviol, key=lambda x: x.slack_hold)[0]
    else:
        rep = cons[0] if cons else (res.paths[0] if res.paths else None)

    if viol or hviol:
        bits = []
        if viol:
            bits.append(f"建立违例 {len(viol)} 条")
        if hviol:
            bits.append(f"保持违例 {len(hviol)} 条")
        tag, tagcls = " / ".join(bits), "bad"
    elif cons:
        tag, tagcls = "全部满足", "ok"
    else:
        tag, tagcls = "无已约束路径", "warn"

    parts: list[str] = []
    parts.append(f'<section class="card" id="{html.escape(anchor)}">')
    parts.append("<header>")
    parts.append(f'<h2>{html.escape(meta.get("title", design.top))}</h2>')
    parts.append(f'<span class="tag">{html.escape(meta.get("key", design.top))}</span>')
    parts.append(f'<span class="tag {tagcls}">{tag}</span>')
    parts.append(f'<span class="tag">主题：{html.escape(meta.get("topic", "-"))}</span>')
    if meta.get("desc"):
        parts.append(f'<p class="desc">{meta["desc"]}</p>')
    parts.append("</header>")

    parts.append('<div class="grid">')
    parts.append("<div>")

    # 约束源码
    parts.append("<pre>" + _sdc_html(design) + "</pre>")

    # 关键数字
    if rep is not None:
        pt = rep
        p = pt.path
        ok = pt.slack_setup is not None and pt.slack_setup >= 0
        cls = "ok" if ok else ("bad" if pt.slack_setup is not None else "")
        parts.append("<dl class='kv'>")
        rows = [
            ("路径类别", html.escape(p.kind), ""),
            ("起点", html.escape(p.startpoint.replace("@port:", "")), ""),
            ("终点", html.escape(p.endpoint.replace("@port:", "")), ""),
            ("发送沿 / 捕获沿",
             f"{fx(pt.launch_edge)}ns / {fx(pt.capture_edge_setup)}ns", ""),
        ]
        if pt.capture_edge_hold is not None:
            rows.append(("保持检查沿", f"{fx(pt.capture_edge_hold)}ns", ""))
        rows += [
            (f"起点代价 ({p.launch_cost_name or 'Tco'})", f"{fx(p.launch_cost)}ns", ""),
            ("组合逻辑延时", f"{fx(p.comb_delay)}ns", ""),
            ("数据到达时间", f"{fx(pt.arr)}ns", ""),
        ]
        if pt.req_setup is not None:
            rows.append(("数据需求时间", f"{fx(pt.req_setup)}ns", ""))
        rows.append(("**时序预算 budget**", f"{fx(pt.budget_setup)}ns", "hl"))
        if pt.slack_setup is not None:
            rows.append(("**slack (setup)**", f"{float(pt.slack_setup):+.4f}ns", cls))
        if pt.slack_hold is not None:
            rows.append(("slack (hold)", f"{float(pt.slack_hold):+.4f}ns",
                         "ok" if pt.slack_hold >= 0 else "bad"))
        if pt.m_setup != 1 or pt.m_hold != 0:
            rows.append(("多周期 (setup/hold)", f"{pt.m_setup} / {pt.m_hold}", ""))

        for k, v, c in rows:
            kk = k.replace("**", "")
            bold = "font-weight:700;" if "**" in k or c == "hl" else ""
            vv = f'<dd class="{c}" style="{bold}">{v}</dd>'
            if c == "hl":
                vv = f'<dd style="color:#4338ca;{bold}">{v}</dd>'
            parts.append(f"<dt>{html.escape(kk)}</dt>{vv}")
        parts.append("</dl>")

        if meta.get("expect"):
            parts.append(f'<p class="note">{meta["expect"]}</p>')
    parts.append("</div>")
    parts.append("</div>")

    # 波形图整行铺开，看得清
    if rep is not None:
        parts.append('<div class="wavewrap">')
        parts.append(timing_waveform(design, rep, width=1180.0))
        parts.append("</div>")

    un = [p for p in res.paths if p.slack_setup is None and not p.path.setup_disabled]
    if un:
        parts.append(f'<p class="note">另有 {len(un)} 条路径未加 I/O 约束，'
                     f'未参与时序检查（例如 '
                     f'<span class="mono">{html.escape(un[0].path.startpoint.replace("@port:", ""))}'
                     f' → {html.escape(un[0].path.endpoint.replace("@port:", ""))}</span>）。</p>')

    # 全部路径表
    parts.append("<details><summary>展开：该例所有路径</summary>")
    parts.append("<table><thead><tr><th>类别</th><th>起点</th><th>终点</th>"
                 "<th>组合</th><th>预算</th><th>slack</th><th>hold</th>"
                 "<th>判定</th></tr></thead><tbody>")
    for pt in sorted(res.paths, key=lambda x: (x.slack_setup is None,
                                               x.slack_setup if x.slack_setup is not None
                                               else ZERO)):
        p = pt.path
        if pt.slack_setup is None:
            cls, sv = "mute", "-"
        elif pt.slack_setup >= 0:
            cls, sv = "ok", f"{float(pt.slack_setup):+.4f}"
        else:
            cls, sv = "bad", f"{float(pt.slack_setup):+.4f}"
        hv = ("-" if pt.slack_hold is None
              else f"{float(pt.slack_hold):+.4f}")
        hcls = ("" if pt.slack_hold is None else ("ok" if pt.slack_hold >= 0 else "bad"))
        parts.append(
            f"<tr><td>{html.escape(p.kind)}</td>"
            f"<td class='mono'>{html.escape(p.startpoint.replace('@port:', ''))}</td>"
            f"<td class='mono'>{html.escape(p.endpoint.replace('@port:', ''))}</td>"
            f"<td>{fx(pt.comb_delay, 4)}</td>"
            f"<td>{fx(pt.budget_setup, 4)}</td>"
            f"<td class='{cls}'>{sv}</td>"
            f"<td class='{hcls}'>{hv}</td>"
            f"<td class='{cls}'>{pt.verdict}</td></tr>")
    parts.append("</tbody></table></details>")

    parts.append("</section>")
    return "\n".join(parts)


def render_html(reports: list[tuple[Design, AnalysisResult]],
                metas: list[dict] | None = None,
                checks: list[dict] | None = None,
                title: str = "pysta · 时序分析结果") -> str:
    """``metas`` 与 ``reports`` 顺序一一对应（两个例子可能共用同一张网表，
    所以不能用 module 名当 key）。"""
    metas = metas or []
    out: list[str] = []
    out.append("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    out.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    out.append(f"<title>{html.escape(title)}</title>")
    out.append(f"<style>{_CSS}</style></head><body><div class='wrap'>")
    out.append(f"<h1>{html.escape(title)}</h1>")
    out.append(
        "<p class='sub'>每个示例都由 <code>examples/</code> 下的工艺库、"
        "门级网表和 SDC 算出来：解析 Liberty 单元延时与 Tsu/Th/Tco，"
        "构建时序图，枚举路径，按发送沿/捕获沿关系算 slack。"
        "全部结果用 <code>fractions.Fraction</code> 精确计算，不依赖任何商业 EDA 工具。</p>")

    # 总览表
    if checks:
        out.append("<section class='card'><header><h2>总览：期望值 vs 实测值</h2>")
        n_ok = sum(1 for c in checks if c["ok"])
        out.append(f"<span class='tag {'ok' if n_ok == len(checks) else 'bad'}'>"
                   f"{n_ok}/{len(checks)} 项吻合</span>")
        out.append("</header><table><thead><tr><th>示例</th><th>约束</th>"
                   "<th>期望值</th><th>实测值</th><th>结论</th></tr></thead><tbody>")
        for c in checks:
            cls = "ok" if c["ok"] else "bad"
            out.append(f"<tr><td class='mono'>{html.escape(c['example'])}</td>"
                       f"<td>{html.escape(c['what'])}</td>"
                       f"<td class='mono'>{html.escape(c['expected'])}</td>"
                       f"<td class='mono'>{html.escape(c['actual'])}</td>"
                       f"<td class='{cls}'>{'✓' if c['ok'] else 'X'}</td></tr>")
        out.append("</tbody></table></section>")

    for i, (design, res) in enumerate(reports):
        out.append(_card(design, res, metas[i] if i < len(metas) else {},
                         _anchor(metas, i, design)))

    out.append("<p class='sub' style='margin-top:28px'>"
               "由 <code>pysta</code> 生成，纯 SVG，无第三方依赖。</p>")
    out.append("</div></body></html>")
    return "\n".join(out)


def _anchor(metas: list[dict], i: int, design: Design) -> str:
    """给每张卡片一个唯一的错点。

    不能直接用 module 名：两个示例可能共用同一张网表（如 ex9 与 ex9b），
    那样 HTML 里就会出现重复 id。
    """
    base = (metas[i].get("key") if i < len(metas) else None) or design.top
    return f"{base}--{i}"


def write_html(path, reports, **kw) -> None:
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_html(reports, **kw), encoding="utf-8")
