# ==========================================================================
# 例 6：多时钟 —— 输出端口与 -add_delay
#
# clkb 是本设计唯一的真实时钟；clkc / clkd 是下游两个接收时钟，
# 没有实体端口，所以要建成虚拟时钟。
#
# 【-add_delay 的语义】同一个端口上挂多条同类约束时，
# 第二条必须加 -add_delay，否则会把第一条**覆盖掉**：
#     set_output_delay -max 2.5 -clock clkc             [get_ports B]
#     set_output_delay -max 4.5 -clock clkd -add_delay  [get_ports B]   <- 保留第一条
#
# 【Tcl 整数除法】clkc = 300MHz/4 = 75MHz，周期要用实数才能算对：
#     [expr 1/75*1000]   -> 0          （整数除法，先算 1/75 = 0）
#     [expr 1.0/75*1000] -> 13.33333   （实数运算，精确值 40/3 ns）
# 本工具严格实现了这个语义，所以下面这条能拿到正确的 40/3 ns。
#
# 公共基本周期 = LCM(20, 40/3, 10) = 40ns，遍历后取最严格：
#     对 clkc：发送沿 0ns  -> 捕获沿 40/3 = 13.333ns：预算 13.333 - 2.5 - 1.0 = 9.833
#              发送沿 20ns -> 捕获沿 80/3 = 26.667ns：预算  6.667 - 2.5 - 1.0 = 3.167  <-- 最严格
#     对 clkd：发送沿 0ns  -> 捕获沿 10ns           ：预算 10    - 4.5 - 1.0 = 4.5
# 最终 budget = 3.1667ns。
#
# 注意：最严格的是 -max 数值**更小**的 clkc 那条，不是 4.5 的 clkd 那条。
# ==========================================================================

create_clock -period [expr 1.0/75*1000] -name clkc
create_clock -period 10 -name clkd
create_clock -period 20 [get_ports clkb]

set_output_delay -max 2.5 -clock clkc [get_ports B]
set_output_delay -max 4.5 -clock clkd -add_delay [get_ports B]
