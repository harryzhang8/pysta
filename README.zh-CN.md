# pysta

**纯 Python 实现的静态时序分析（STA）引擎，零第三方依赖。**

[![CI](https://github.com/harryzhang8/pysta/actions/workflows/ci.yml/badge.svg)](https://github.com/harryzhang8/pysta/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

`pysta` 自己解析 **Liberty 工艺库**、**门级 Verilog 网表** 和
**SDC 约束文件**，建时序图、枚举路径、算建立/保持 slack —— 全程只用
Python 标准库。

> [English README](README.md)

---

## 为什么要做这个

商业 STA 工具是闭源且庞大的。如果你想真正搞明白"为什么写了
`set_multicycle_path -setup 6` 就必须补一条 `-hold 5`"，
或者"为什么 SDC 里的 `[expr 1/75*1000]` 会算成 0"，
通常只能去翻厂商手册。

`pysta` 把有意思的部分用大约两千行可读的 Python 重新实现了一遍，
你可以单步调试、打印中间值，也可以故意改坏它看看会怎样。

---

## 安装

```bash
pip install pysta
```

也可以直接用源码跑 —— **没有任何第三方运行时依赖**：

```bash
git clone https://github.com/harryzhang8/pysta
cd pysta
python -m pysta check      # 不装包也能跑
```

---

## 快速开始

```bash
pysta list                 # 列出所有示例
pysta check                # 跑全部示例并核对期望值
pysta report ex9           # 打印 report_timing 风格的报告
pysta run                  # 输出报告 + 交互式 HTML 到 ./out/
```

`pysta run` 产出：

| 文件 | 内容 |
|---|---|
| `out/timing_report.txt` | 所有示例的 `report_timing` 风格完整报告 |
| `out/checks.txt` | 期望值 vs 实测值对照表 |
| `out/timing_visualization.html` | 自包含的 SVG 时序波形 + slack 条形图 |

`pysta check` 就是 CI 跑的东西：任何一条对不上就返回非零。

---

## Python API

```python
from pysta import Design, analyze
from pysta.paths import DEFAULT_LIB, NETLIST_DIR, SDC_DIR

design = Design.build(
    DEFAULT_LIB,
    NETLIST_DIR / "ex1_reg2reg.v",
    SDC_DIR / "ex1_reg2reg.sdc",
)
result = analyze(design)

for pt in result.worst_setup(3):
    print(f"{pt.path.startpoint} -> {pt.path.endpoint}")
    print(f"  到达时间 = {float(pt.arr):.4f} ns")
    print(f"  需求时间 = {float(pt.req_setup):.4f} ns")
    print(f"  时序预算 = {float(pt.budget_setup):.4f} ns")
    print(f"  slack    = {float(pt.slack_setup):+.4f} ns  ({pt.verdict})")
```

所有时间量都是 `fractions.Fraction`，**精确**无误差：

```python
from fractions import Fraction
design.sdc.clocks["clkc"].period == Fraction(40, 3)   # True，不是 13.333333
```

---

## 实现了什么

**Liberty (`.lib`)** —— 通用 group/属性文法解析器；NLDM 一维/二维查表，
支持双线性插值与线性外推；cell/pin 定义、`ff` 组、setup/hold 约束、
clock-to-Q 时序弧。

**Verilog 网表** —— 结构化门级网表：模块端口、总线
（`input [7:0] A;` 展开成 `A[7]..A[0]`）、数组例化名（`FF3[0]`）、
命名与位置连接、`assign` 网络别名。遇到行为级 RTL 会明确报错，而不是猜。

**SDC / Tcl** —— `create_clock`（含虚拟时钟与自定义波形）、
`set_clock_uncertainty`、`set_clock_transition`、`set_clock_latency`、
`set_propagated_clock`、`set_input_delay`、`set_output_delay`、`set_max_delay`、
`set_false_path`、`set_clock_groups`、`set_multicycle_path`，以及
`get_ports` / `get_pins` / `get_clocks` / `get_cells` / `all_clocks`
和 Tcl `expr` 求值器。

**分析** —— 时序图构建、时钟网络穿越缓冲器的传播、起点到终点之间的路径枚举、
在所有时钟的**公共基本周期**内取最差建立/保持，以及报告输出。

---

## 示例

每个示例只讲一个时序概念，并且附带一条对时序预算的断言，所以它们同时也是回归测试。

| 示例 | 概念 | 期望预算 |
|---|---|---|
| `ex0_clk_attr` | 不确定度 / 转换时间 / 延时建模 | 7.5 ns |
| `ex1_reg2reg` | 寄存器到寄存器，只靠 `create_clock` 约束 | 8.0 ns |
| `ex2_in2reg` | `set_input_delay` | 11.6 ns |
| `ex3_reg2out` | `set_output_delay` | 12.6 ns |
| `ex4_in2out` | 纯组合端口到端口路径 | 18.8 ns |
| `ex5_multi_clk_in` | 虚拟时钟、两个发送沿取最差 | 3.5 ns |
| `ex6_multi_clk_out` | 两个捕获时钟 + `-add_delay` 覆盖规则 | 3.1667 ns |
| `ex7_async_cdc` | 异步时钟之间的 `set_false_path` | 屏蔽 2 条 |
| `ex8_pseudo_path` | 逻辑伪路径、多个 `-through` 的「且」语义 | 屏蔽 2 保留 2 |
| `ex9_multicycle_adder` | 多周期路径（正确写法） | 58 ns，保持沿 0 ns |
| `ex9b_..._default_hold` | **反例**：只写 `-setup 6` 不写 `-hold 5` | 保持沿停在 50 ns → −49 ns |
| `ex10_multicycle_mixed` | 多周期与单周期路径共存 | 17.9 / 7.9 ns |

每个示例在 `src/pysta/examples/` 下有三份文件：
`rtl/*.v`（设计意图）、`netlist/*.v`（门级网表，STA 真正的输入）、
`sdc/*.sdc`（约束，注释很详细）。

---

## 设计要点

### 时钟沿关系

引擎里最容易写错的一段。设发送沿在 `t_L`，`i0` 是捕获时钟在 `t_L` **之后第一个**
上升沿的序号，则：

```
建立捕获沿序号 = i0 + (M_setup - 1)
保持捕获沿序号 = 建立捕获沿序号 - 1 - M_hold
```

`M_setup` 默认 1、`M_hold` 默认 0，于是建立检查在下一个沿、保持检查在**与发送沿
同一时刻**的那个沿 —— 这才是"保持时间分析比建立时间分析提前一个时钟周期沿"的真正含义。

在 10ns 时钟上代入 `set_multicycle_path -setup 6`（M_setup=6, M_hold=0）：
建立沿到 60ns，**同时把保持沿拖到 50ns**；再补 `-hold 5` 把它拉回
`6 - 1 - 5 = 0`。详见
[docs/multicycle-edge-relationships.md](docs/multicycle-edge-relationships.md)。

### 公共基本周期

有多个时钟时，引擎会遍历 `LCM(T1, T2, ...)` 内的**每一个**发送沿并取最差：

* `LCM(30, 20) = 60 ns` → 发送沿 {0, 30}，最差的一对是 30 → 40；
* `LCM(20, 40/3, 10) = 40 ns` → 最差的一对是 20 → 80/3。

这就是"直接把两个周期相减"会算错的原因。

### 精确有理数运算

周期、延时、延迟一律用 `Fraction`，绝不用 `float`。`1.0/75*1000` 得到精确的
`40/3`，于是 `40/3 * 3 == 40`。用浮点会得到 `39.99999999999999`，
算出的公共基本周期就悄悄错了。

### Tcl 的整数除法

`expr` 刻意保留 Tcl 语义：`1/75` 是整数除法，得到 `0`；`1.0/75` 才是浮点。
这是 SDC 里非常经典的静默 bug，所以求值器会跟踪每个值"是不是整数"并据此截断。

### `-add_delay` 的语义

```tcl
set_output_delay -max 2.5 -clock clkc [get_ports B]
set_output_delay -max 4.5 -clock clkd -add_delay [get_ports B]
```

不加 `-add_delay` 时，第二条会**覆盖**第一条。解析器实现了这个规则并在覆盖时给出警告；
分析器会把**每一条存留下来的约束分别算一遍**再取最差。`ex6` 里起决定作用的是
`clkc` 的 2.5ns，而不是数值更大的 `clkd` 的 4.5ns。

### 单元延时与负载相关

`Tco` 和普通组合时序弧一样走 NLDM 查表，所以它随扇出增大。`ex10` 里 `FF1/Q`
驱动两个负载，`Tco` 从 1.0ns 变成 1.1ns，于是 2 周期的预算变成 17.9ns 而不是想当然的 18.0ns。

---

## 局限

`pysta` 是**教学引擎**，不能替代 PrimeTime / OpenSTA。

| | pysta | 生产级 STA |
|---|---|---|
| 延时模型 | 单元查表 + 线负载模型（默认理想连线） | 加上寄生参数提取（SPEF） |
| 时钟树 | 用 SDC 里的 latency 数字，不做真实传播 | 按实际时钟树传播 |
| 路径搜索 | 穷举简单路径 | 时序图最差路径算法 |
| 时序弧 | 以 max/建立为主，保持只算寄存器终点 | 完整 max/min，上升/下降分别建模 |
| 优化 | 只报不改 | 改尺寸、插缓冲器、重定时 |
| 串扰 / SI | 不建模 | 完整 SI 分析 |

**不要用它给真实设计签核。**

---

## 开发

```bash
pip install -e ".[dev]"
pytest                      # 137 个测试
pysta check                 # 32 条期望值核对
```

### 可选：真实综合与仿真

`tools/rtl_check.py` 会用 `iverilog` 查语法、用 `yosys` 真综合一遍，
顺带证明这些示例是可综合的：

```bash
python tools/fetch_oss_cad_suite.py    # 下载 oss-cad-suite（约 570MB，免安装）
python tools/rtl_check.py              # iverilog + yosys，11/11 通过
```

> **Windows 上的一个坑**：oss-cad-suite 里的 yosys/abc 在非 ASCII 路径下会
> 报 `init_share_dirname: unable to determine share/ directory`。所以
> `fetch_oss_cad_suite.py` 默认装到 `%LOCALAPPDATA%\pysta-tools\oss-cad-suite`，
> `rtl_check.py` 也会先把源文件复制到纯 ASCII 的临时目录再跑。

---

## 参与贡献

欢迎提 issue 和 PR。一些不错的切入点：

* 更多 SDC 命令（`create_generated_clock`、`set_case_analysis`、`set_disable_timing`）
* 输出端口路径的 min delay / 纯保持分析
* 分频时钟与生成时钟的示例
* 把代码注释翻译成英文（目前是中文）

提交 PR 前请先跑 `pytest` 和 `pysta check`。

---

## 许可

[Apache License 2.0](LICENSE) © 2026 harryzhang8
