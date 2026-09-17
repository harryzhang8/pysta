# -*- coding: utf-8 -*-
"""
RTL 可综合性检查（可选，需要 oss-cad-suite）
===========================================

用 iverilog 做语法检查、用 yosys 做一次真正的综合，确认示例 RTL 是**可综合**的。
本项目的主流程（SDC + STA）不依赖这个脚本，但它能回答一个很实际的问题：
"这些 RTL 是真的能综合出电路，还是随手写的？"

用法::

    python tools/rtl_check.py                    # 检查示例的 RTL
    python tools/rtl_check.py --netlist          # 顺便也检查门级网表
    python tools/rtl_check.py --suite D:\\oss    # 指定 oss-cad-suite 位置

工具查找顺序：
    1. 命令行 --suite
    2. 环境变量 OSS_CAD_SUITE
    3. %LOCALAPPDATA%\\pysta-tools\\oss-cad-suite          ← fetch 脚本的默认位置
    4. <项目>/tools/oss-cad-suite

⚠️ Windows 上的一个坑
--------------------
oss-cad-suite 里的 yosys / abc 对**非 ASCII 路径**支持不好（表现为
"init_share_dirname: unable to determine share/ directory"）。
如果你的工程放在中文目录下，本脚本会自动把源文件复制到
`%LOCALAPPDATA%\\pysta-rtlcheck\\` 这个纯 ASCII 目录里再跑。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
EXAMPLES = PROJ / "src" / "pysta" / "examples"
RTL_DIR = EXAMPLES / "rtl"
NETLIST_DIR = EXAMPLES / "netlist"

_MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_]\w*)", re.MULTILINE)


# --------------------------------------------------------------------------


def candidate_suites(cli: str | None) -> list[Path]:
    out: list[Path] = []
    if cli:
        out.append(Path(cli))
    if os.environ.get("OSS_CAD_SUITE"):
        out.append(Path(os.environ["OSS_CAD_SUITE"]))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "pysta-tools" / "oss-cad-suite")
    out.append(HERE / "oss-cad-suite")
    out.append(Path("C:/tools/oss-cad-suite"))
    return out


def find_suite(cli: str | None) -> Path | None:
    for p in candidate_suites(cli):
        if (p / "bin").is_dir():
            return p.resolve()
    return None


def suite_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["YOSYSHQ_ROOT"] = str(root) + os.sep
    env["SSL_CERT_FILE"] = str(root / "etc" / "cacert.pem")
    env["PATH"] = str(root / "bin") + os.pathsep + str(root / "lib") + os.pathsep + env["PATH"]
    return env


def tools_of(root: Path) -> tuple[Path, Path]:
    exe = ".exe" if os.name == "nt" else ""
    return root / "bin" / f"iverilog{exe}", root / "bin" / f"yosys{exe}"


# --------------------------------------------------------------------------


def is_ascii(p: Path) -> bool:
    try:
        str(p).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def ascii_stage(paths: list[Path]) -> tuple[Path, list[Path]]:
    """把源文件复制到纯 ASCII 的临时目录（非 ASCII 路径下 yosys 会挂）。"""
    staging = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "pysta-rtlcheck"
    staging.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for p in paths:
        dst = staging / p.name
        shutil.copyfile(p, dst)
        copied.append(dst)
    return staging, copied


def module_of(text: str, stem: str) -> str | None:
    mods = _MODULE_RE.findall(text)
    if not mods:
        return None
    return stem if stem in mods else mods[0]


def run(cmd: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    try:
        r = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=env,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError as exc:
        return 127, f"找不到可执行文件: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "超时"


# --------------------------------------------------------------------------


def check(files: list[Path], suite: Path, want_netlist: bool) -> int:
    iverilog, yosys = tools_of(suite)
    env = suite_env(suite)

    for f in files:
        if not iverilog.exists():
            break
    if not iverilog.exists() or not yosys.exists():
        print(f"! 在 {suite} 里没找齐 iverilog / yosys")
        return 2

    # 决定是否需要绕开非 ASCII 路径
    need_stage = any(not is_ascii(f) for f in files)
    if need_stage:
        print("检测到非 ASCII 路径，先把源文件复制到临时 ASCII 目录再跑\n"
              f"  staging = {Path(os.environ.get('LOCALAPPDATA', '')) / 'pysta-rtlcheck'}\n")
        cwd, staged = ascii_stage(files)
    else:
        cwd, staged = PROJ, files

    ok = 0
    fails: list[str] = []
    print(f"工具链: {suite}")
    print(f"iverilog: {iverilog.name}    yosys: {yosys.name}\n")
    print(f"  {'文件':<38}{'模块':<26}{'语法':<8}{'综合':<8}单元数")
    print("  " + "-" * 100)

    for src, f in zip(files, staged):
        text = f.read_text(encoding="utf-8", errors="replace")
        mod = module_of(text, f.stem) or f.stem

        rc1, out1 = run([iverilog, "-t", "null", f.name], cwd, env)
        # -Q 只是关掉 yosys 的启动 banner，stat 的输出要保留才好统计单元数
        rc2, out2 = run([yosys, "-Q", "-p",
                         f"read_verilog {f.name}; synth -top {mod}; stat"], cwd, env)

        ncell = ""
        # yosys 的 stat 有新旧两种排版，都兼容一下
        for pat in (r"^\s+(\d+)\s+cells\s*$",
                    r"Number of cells:\s+(\d+)"):
            m = re.search(pat, out2, re.MULTILINE)
            if m:
                ncell = m.group(1)
                break
        seq = ""
        m = re.search(r"^\s+(\d+)\s+\$_?(?:DFF|SDFF|DFFE)\w*\s*$", out2, re.MULTILINE)
        if m:
            seq = f"（含 {m.group(1)} 个触发器）"
        ncell = ncell + seq

        syntax = "OK" if rc1 == 0 else "FAIL"
        synth = "OK" if rc2 == 0 else "FAIL"
        rel = src.relative_to(PROJ) if PROJ in src.parents else src
        print(f"  {str(rel):<38}{mod:<26}{syntax:<8}{synth:<8}{ncell}")

        if rc1 != 0:
            fails.append(f"{rel} 语法错误:\n" + "\n".join(out1.splitlines()[:6]))
        elif rc2 != 0:
            fails.append(f"{rel} 综合失败:\n" + "\n".join(out2.splitlines()[:6]))
        else:
            ok += 1

    print()
    print(f"  通过 {ok}/{len(files)}")
    for msg in fails:
        print("\n! " + msg)
    return 0 if not fails else 1


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:                                   # pragma: no cover
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", default=None, help="oss-cad-suite 目录")
    ap.add_argument("--netlist", action="store_true", help="同时检查 netlist/")
    args = ap.parse_args()

    suite = find_suite(args.suite)
    if suite is None:
        print("没找到 oss-cad-suite。先运行：\n"
              "    python tools/fetch_oss_cad_suite.py\n"
              "或者用 --suite 指定已有安装位置。")
        return 2

    files = sorted(RTL_DIR.glob("*.v"))
    if args.netlist:
        files += sorted(NETLIST_DIR.glob("*.v"))
    if not files:
        print("没有找到 .v 文件")
        return 2
    return check(files, suite, args.netlist)


if __name__ == "__main__":
    sys.exit(main())
