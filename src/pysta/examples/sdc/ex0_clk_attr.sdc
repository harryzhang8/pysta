# ==========================================================================
# 例 0：时钟属性的约束写法
#
# 【时钟不确定度】skew 与 jitter 都归到 uncertainty 里建模：
#     set_clock_uncertainty -setup 0.5 [get_clocks clk]
#   不加 -setup / -hold 时，建立和保持会用同一个值；
#   也可以分别给上升/下降沿：
#     set_clock_uncertainty -rise 0.2 -fall 0.5 [get_clocks clk]
#
# 【时钟转换时间】一般只约束最大值：
#     set_clock_transition -max 0.2 [get_clocks clk]
#
# 【时钟延时】分成两段：
#     源延迟 source latency：时钟源 -> 芯片时钟引脚（工具无法自己知道）
#     网络延迟 network latency：芯片时钟引脚 -> 寄存器 CP（布线后才能算）
#   布线前：
#     set_clock_latency -source -max 3 [get_clocks clk]
#     set_clock_latency 1 [get_clocks clk]
#   布线后，网络延迟由实际时钟树得出，用下面这条取代上面第二条：
#     set_propagated_clock [get_clocks clk]
#
#   两者要分开约束的原因：source latency 是工具算不出来的、必须人为给定的；
#   而 network latency 只有在布局布线之后才有真实值。布线后再去人为约束网络
#   延迟反而会掩盖时钟树的真实情况。
#
# 本例效果（budget = Tclk - Tco - Tsu - uncertainty）：
#     不设 uncertainty              -> 10 - 1.0 - 1.0       = 8.0ns
#     设 set_clock_uncertainty 0.5 -> 10 - 1.0 - 1.0 - 0.5 = 7.5ns
#   source latency 发送端/捕获端共有，会相互抵消，不影响 slack。
# ==========================================================================

create_clock -period 10 [get_ports clk]

set_clock_uncertainty -setup 0.5 [get_clocks clk]
set_clock_transition  -max 0.2 [get_clocks clk]

set_clock_latency -source -max 3 [get_clocks clk]
set_clock_latency 1 [get_clocks clk]

# 分别给上升沿/下降沿建模不确定度（会覆盖上面的 -setup 值）：
# set_clock_uncertainty -rise 0.2 -fall 0.5 [get_clocks clk]

# 布局布线之后，网络延迟由实际时钟树计算，取代 set_clock_latency：
# set_propagated_clock [get_clocks clk]
