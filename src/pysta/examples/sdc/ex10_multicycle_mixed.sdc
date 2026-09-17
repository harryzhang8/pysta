# ==========================================================================
# 例 10：多周期路径与单周期路径共存
#
# 设计规格：乘法要 2 个周期，异或只要默认的 1 个周期。
# 用 -through 把多周期约束**只**加在乘法那条路径上：
#
#     set_multicycle_path -setup 2 -from FF1/CP -through Multiply/out -to FF2/D
#     set_multicycle_path -hold  1 -from FF1/CP -through Multiply/out -to FF2/D
#
# 如果去掉 -through，两条路径都会变成 2 周期，异或那条就被放松了 ——
# 可能综合出一条实际跑不到 1 个周期的电路。
#
# 沿关系：
#     乘法路径：建立沿 = 第 2 个上升沿 = 20ns；-hold 1 把保持沿拉回 0ns
#                => Tco 是 1.1ns（FF1/Q 扇出为 2，查表得出的 Tco 随负载变大）
#                => budget = 20 - 1.1 - 1.0 = 17.9ns
#     异或路径（普通路径）：仍是默认 1 个周期
#                => budget = 10 - 1.1 - 1.0 = 7.9ns
#
# 这样“同一对寄存器之间两条路径、不同周期数”就能被正确区分开。
# ==========================================================================

create_clock -period 10 [get_ports clk]

set_multicycle_path -setup 2 -from FF1/CP -through Multiply/out -to FF2/D
set_multicycle_path -hold  1 -from FF1/CP -through Multiply/out -to FF2/D
