# -*- coding: utf-8 -*-
"""
下载并解压 oss-cad-suite（可选的开源 Verilog 工具链）
====================================================

本项目**不依赖**任何外部 EDA 工具就能跑通 RTL -> SDC -> STA 全流程
（`sta/` 是纯 Python 实现）。但如果你想让流程更"真"一点 ——
用 yosys 真综合一次、用 iverilog 真仿真一次 —— 可以跑这个脚本。

oss-cad-suite 是一个**免安装、免管理员权限**的可移植包，解压即用，包含：
    yosys      综合（RTL -> 门级网表）
    iverilog   Verilog 仿真
    verilator  高性能仿真/静态检查
    gtkwave    看波形
    sby        形式验证

用法::

    python tools/fetch_oss_cad_suite.py            # 下载到 tools/oss-cad-suite/
    python tools/fetch_oss_cad_suite.py --keep     # 保留下载的 .tgz

下载完成后，脚本会打印需要设置的环境变量，并自动探测 yosys / iverilog 是否可用。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

REPO = "YosysHQ/oss-cad-suite-build"
HERE = Path(__file__).resolve().parent


def default_dest() -> Path:
    """默认装到**纯 ASCII** 目录。

    这是踩过的坑：oss-cad-suite 里的 yosys / abc 在非 ASCII 路径下会报
    ``init_share_dirname: unable to determine share/ directory``。
    工程本身放在中文目录里没问题，工具链必须放英文路径。
    """
    local = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local:
        return Path(local) / "pysta-tools" / "oss-cad-suite"
    return HERE / "oss-cad-suite"


DEST = default_dest()


def _api(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "pysta", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def find_asset() -> tuple[str, str, int]:
    """返回 (版本号, 下载地址, 字节数)。"""
    rel = _api(f"https://api.github.com/repos/{REPO}/releases/latest")
    tag = rel["tag_name"]
    for a in rel["assets"]:
        name = a["name"].lower()
        if "windows" in name and "x64" in name and name.endswith(".tgz"):
            return tag, a["browser_download_url"], a["size"]
    raise SystemExit("没有找到 windows-x64 的发布包")


def download(url: str, out: Path, total: int) -> None:
    print(f"下载: {url}")
    print(f"大小: {total / 1024 / 1024:.1f} MB  ->  {out}")
    tmp = out.with_suffix(out.suffix + ".part")
    got = 0
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "pysta"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as fh:
        while True:
            chunk = resp.read(1 << 20)          # 1MB
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            if got % (16 << 20) < (1 << 20):
                speed = got / max(time.time() - t0, 1e-6) / 1024 / 1024
                pct = got * 100 / total if total else 0
                print(f"\r  {got / 1048576:7.1f} / {total / 1048576:.1f} MB"
                      f"  ({pct:5.1f}%)  {speed:5.1f} MB/s", end="", flush=True)
    print()
    tmp.replace(out)


def extract(tgz: Path, dest: Path) -> None:
    print(f"解压到 {dest} ...")
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tgz, "r:gz") as tf:
        members = tf.getmembers()
        # 包里顶层是一个目录，这里把它剥掉直接铺到 dest
        tops = {m.name.split("/", 1)[0] for m in members}
        strip = tops.pop() if len(tops) == 1 else None
        for m in members:
            if strip and m.name == strip:
                continue
            if strip and m.name.startswith(strip + "/"):
                m.name = m.name[len(strip) + 1:]
            if not m.name:
                continue
            try:
                tf.extract(m, dest, filter="data")      # Python 3.12+
            except TypeError:
                tf.extract(m, dest)
    print("解压完成")


def probe(dest: Path) -> None:
    env = os.environ.copy()
    env["PATH"] = str(dest / "bin") + os.pathsep + env["PATH"]
    print("\n工具探测：")
    for tool, args in [("yosys", ["-V"]), ("iverilog", ["-V"]), ("verilator", ["--version"])]:
        exe = dest / "bin" / f"{tool}.exe"
        if not exe.exists():
            exe = dest / "bin" / tool
        if not exe.exists():
            print(f"  {tool:10s} 未找到")
            continue
        try:
            out = subprocess.run([str(exe), *args], capture_output=True, text=True,
                                 timeout=30, env=env)
            first = (out.stdout or out.stderr).strip().splitlines()
            print(f"  {tool:10s} {first[0] if first else '(无输出)'}")
        except Exception as exc:                     # pragma: no cover
            print(f"  {tool:10s} 调用失败: {exc}")

    print("\n后续用法（PowerShell）：")
    print(f'  $env:PATH = "{dest / "bin"};$env:PATH"')
    print("  yosys -p \"read_verilog rtl/ex1_reg2reg.v; synth; write_verilog netlist/yosys_ex1.v\"")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", type=Path, default=DEST)
    ap.add_argument("--keep", action="store_true", help="保留下载的 .tgz")
    args = ap.parse_args()

    if (args.dest / "bin").exists():
        print(f"已经装过了：{args.dest}")
        probe(args.dest)
        return 0

    tag, url, size = find_asset()
    tgz = HERE / f"oss-cad-suite-{tag}.tgz"
    if not tgz.exists():
        download(url, tgz, size)
    else:
        print(f"复用已有安装包 {tgz}")

    extract(tgz, args.dest)
    if not args.keep:
        tgz.unlink(missing_ok=True)
    probe(args.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
