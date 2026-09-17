# ==========================================================================
# 例 9：多周期路径（正确写法）
#
# 沿关系推导（本工具的实现方式，与 DC/PrimeTime 语义一致）：
#     设发送沿序号为 k，捕获时钟在它之后第一个上升沿的序号为 i0，则
#         建立捕获沿序号 = i0 + (M_setup - 1)
#         保持捕获沿序号 = 建立捕获沿序号 - 1 - M_hold
#
# 写成 -setup 6 / -hold 5（M_setup=6, M_hold=5），取发送沿 k=0：
#     建立沿 = 第 6 个上升沿 = 60ns
#     保持沿 = 6 - 1 - 5 = 第 0 个上升沿 = 0ns      <- 被 -hold 5 从 50ns 拉回来了
#
#     budget   = 60 - Tco - Tsu - uncertainty = 60 - 1.0 - 1.0 = 58ns
#     保持要求 = Th + uncertainty = 0.5ns
#                （发送沿和捕获沿同一时刻，两端的时钟项直接抵消）
#
# 对比 ex9b_multicycle_adder_default_hold.sdc（不写 -hold 5 的版本）：
#   保持沿会停在 50ns，要求组合逻辑延时 >= 50.5ns，而建立又要求 <= 58ns ——
#   工具会被迫去凑一条延时在 50.5~58ns 之间的长路径，而真实逻辑只有零点几 ns。
#
# 口诀：-hold 的数值 = -setup 的数值 - 1。
# ==========================================================================

create_clock -period 10 [get_ports clk]

set_multicycle_path -setup 6 -to [get_pins FF3[*]/D]
set_multicycle_path -hold  5 -to [get_pins FF3[*]/D]
