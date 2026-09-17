# -*- coding: utf-8 -*-
"""
pysta 命令行
============

::

    pysta list               列出所有示例及其覆盖的时序概念
    pysta check              跑全部示例并核对期望值（CI 用，失败返回非零）
    pysta report [示例名]     打印 report_timing 风格的时序报告
    pysta run                跑全部示例，输出报告 + HTML 可视化到 out/

也可以不装包直接跑：``python -m pysta``
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from . import __version__
from . import paths
from . import report as rep
from . import viz
from .checks import EXAMPLES, Example
from .timing import Design, analyze

BAR = "=" * 78


# --------------------------------------------------------------------------


def _select(only: str | None) -> list[Example]:
    return [e for e in EXAMPLES if not only or only in e.name]


def _build(ex: Example, lib: Path | None = None) -> tuple[Design, object]:
    netlist = paths.NETLIST_DIR / f"{ex.netlist_name}.v"
    sdc = paths.SDC_DIR / f"{ex.sdc_name}.sdc"
    if not netlist.exists():
        raise FileNotFoundError(f"找不到网表 {netlist}")
    if not sdc.exists():
        raise FileNotFoundError(f"找不到约束 {sdc}")
    design = Design.build(lib or paths.DEFAULT_LIB, netlist, sdc)
    return design, analyze(design)


def _meta(ex: Example) -> dict:
    return {
        "key": ex.name,
        "title": ex.title,
        "topic": ex.topic,
        "desc": ex.desc,
        "expect": ex.expect,
    }


# --------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    print(f"pysta 自带 {len(EXAMPLES)} 个示例：\n")
    print(f"  {'名称':<36}{'覆盖的概念':<24}说明")
    print("  " + "-" * 100)
    for ex in _select(args.only):
        print(f"  {ex.name:<36}{ex.topic:<24}{ex.title}")
    print("\n用 `pysta check` 核对全部期望值，`pysta report <名称>` 看单例详情。")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    rows: list[dict] = []
    failures: list[str] = []

    for ex in _select(args.only):
        try:
            design, res = _build(ex, args.lib)
        except Exception as exc:                        # noqa: BLE001
            failures.append(f"{ex.name}: 分析失败 ({exc})")
            if args.verbose:
                traceback.print_exc()
            continue
        for chk in ex.checks:
            try:
                ok = chk.ok(design, res)
                actual = chk.show_actual(design, res)
            except Exception as exc:                    # noqa: BLE001
                ok, actual = False, f"<异常: {exc}>"
            rows.append({
                "example": ex.name, "what": chk.what,
                "expected": chk.show(), "actual": actual, "ok": ok,
            })
            if not ok:
                failures.append(f"{ex.name}: {chk.what} —— 期望 {chk.show()}，实际 {actual}")

    n_ok = sum(1 for r in rows if r["ok"])
    print(f"  {'示例':<34}{'约束':<44}{'期望':>12}{'实测':>12}  结论")
    print("  " + "-" * 108)
    for r in rows:
        print(f"  {r['example']:<34}{r['what']:<44}{r['expected']:>12}"
              f"{r['actual']:>12}  {'OK' if r['ok'] else 'FAIL'}")

    print()
    print(f"{BAR}\n 对照项: {n_ok}/{len(rows)} 通过\n{BAR}")
    for f in failures:
        print(f" ! {f}")
    return 0 if not failures else 1


def cmd_report(args: argparse.Namespace) -> int:
    examples = _select(args.only)
    if not examples:
        print(f"没有匹配 {args.only!r} 的示例")
        return 2
    out: list[str] = []
    for ex in examples:
        design, res = _build(ex, args.lib)
        out.append(rep.format_design(design, res, verbose_paths=args.paths))
    text = "\n".join(out)
    if args.output:
        p = Path(args.output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"已写入 {p}")
    else:
        print(text)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out or "out")
    examples = _select(args.only)

    reports: list[tuple[Design, object]] = []
    metas: list[dict] = []
    checks_rows: list[dict] = []
    failures: list[str] = []
    text: list[str] = ["pysta 时序分析报告", BAR]

    for ex in examples:
        try:
            design, res = _build(ex, args.lib)
        except Exception as exc:                        # noqa: BLE001
            failures.append(f"{ex.name}: {exc}")
            traceback.print_exc()
            continue

        print(f"\n{BAR}\n>>> {ex.name}  ——  {ex.title}\n{BAR}")
        print(rep.format_design(design, res, verbose_paths=1))

        reports.append((design, res))
        metas.append(_meta(ex))
        text.append(rep.format_design(design, res, verbose_paths=2))

        for chk in ex.checks:
            try:
                ok = chk.ok(design, res)
                actual = chk.show_actual(design, res)
            except Exception as exc:                    # noqa: BLE001
                ok, actual = False, f"<异常: {exc}>"
            checks_rows.append({
                "example": ex.name, "what": chk.what,
                "expected": chk.show(), "actual": actual, "ok": ok,
            })
            if not ok:
                failures.append(f"{ex.name}: {chk.what} —— 期望 {chk.show()}，实际 {actual}")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "timing_report.txt").write_text("\n".join(text) + "\n", encoding="utf-8")

    ck = ["pysta 期望值核对", BAR,
          f"  {'示例':<34}{'约束':<44}{'期望':>12}{'实测':>12}  结论",
          "  " + "-" * 108]
    for r in checks_rows:
        ck.append(f"  {r['example']:<34}{r['what']:<44}{r['expected']:>12}"
                  f"{r['actual']:>12}  {'OK' if r['ok'] else 'FAIL'}")
    n_ok = sum(1 for r in checks_rows if r["ok"])
    ck += ["", f"  合计: {n_ok}/{len(checks_rows)} 通过"]
    (out_dir / "checks.txt").write_text("\n".join(ck) + "\n", encoding="utf-8")

    viz.write_html(out_dir / "timing_visualization.html", reports,
                   metas=metas, checks=checks_rows,
                   title="pysta · 时序波形与 slack")

    print(f"\n{BAR}")
    print(f" 对照项: {n_ok}/{len(checks_rows)} 通过")
    for f in failures:
        print(f" ! {f}")
    print(BAR)
    print(" 产物:")
    for name in ("timing_report.txt", "checks.txt", "timing_visualization.html"):
        print(f"   {out_dir / name}")
    return 0 if not failures else 1


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="pysta",
        description="纯 Python 的迷你静态时序分析（STA）工具链",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--version", action="version", version=f"pysta {__version__}")
    ap.add_argument("--lib", type=Path, default=None, help="换一个 Liberty 工艺库")
    ap.add_argument("--only", default=None, help="只处理名字包含该子串的示例")

    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="列出所有示例")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("check", help="核对全部期望值（CI 用）")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("report", help="打印时序报告")
    p.add_argument("-o", "--output", default=None, help="写入文件而不是打印")
    p.add_argument("--paths", type=int, default=2, help="每个示例打印几条路径详情")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("run", help="跑全部示例并输出报告 + HTML")
    p.add_argument("--out", default=None, help="输出目录（默认 ./out）")
    p.set_defaults(func=cmd_run)

    return ap


def main(argv: list[str] | None = None) -> int:
    # 保留控制台原有编码（Windows 控制台是 UTF-8，重定向到管道时是本地代码页，
    # 两者都能编码中文）；只把无法编码的字符降级处理，避免直接抛异常。
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:                                   # pragma: no cover
        pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
