# ==========================================================================
# 例 7：时序例外 —— 异步路径
#
# clka（50MHz）和 clkb（100MHz）来自两个独立时钟源，它们之间的相位关系
# 不确定且持续变化，所以跨域路径不应该做时序分析 —— 分析结果没有意义，
# 还会白白占掉工具的优化资源。用 set_false_path 把它们排除掉：
#
#     set_false_path -from [get_clocks clka] -to [get_clocks clkb]
#     set_false_path -from [get_clocks clkb] -to [get_clocks clka]
#
# 两个方向都要写 —— set_false_path 的 -from/-to 是有方向的。
#
# 下面注释的那一条与上面两条等价，一条顶两条：
#     set_clock_groups -asynchronous -group [get_clocks clka] -group [get_clocks clkb]
# （注意命令名是复数 set_clock_groups）
#
# 运行效果：两条跨时钟域路径被标为 EXCLUDED；
#           域内的同源路径不受影响。
#
# 提醒：false_path 只是让工具别去优化它，并不代表电路安全 ——
#       真实设计里跨时钟域还需要同步器或握手逻辑。
# ==========================================================================

create_clock -period 20 [get_ports clka]
create_clock -period 10 [get_ports clkb]

set_false_path -from [get_clocks clka] -to [get_clocks clkb]
set_false_path -from [get_clocks clkb] -to [get_clocks clka]

# set_clock_groups -asynchronous -group [get_clocks clka] -group [get_clocks clkb]
