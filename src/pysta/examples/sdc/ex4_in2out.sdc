# ==========================================================================
# 例 4：输入端口 -> 输出端口（纯组合逻辑路径）
#
# 中间没有寄存器，时钟跟这条路径没关系。约束的方法是：
# 用输入端的 input_delay 和输出端的 output_delay，从两边把中间夹出来。
#
#     budget = Tclk - input_delay - output_delay
#            = 20 - 0.4 - 0.8 = 18.8ns
#
# 另一种写法是直接限制“端口到端口”的延时（文件未尾注释的那条）：
#     set_max_delay 1.2 -from [get_ports C] -to [get_ports D]
# 它会直接卡死这条路径的延时（budget 就变成 1.2ns），和上面两条的效果不同：
# input/output delay 描述的是“两端各占了多少”，set_max_delay 描述的是“中间这段必须多快”。
# ==========================================================================

create_clock -period 20 [get_ports clk]

set_input_delay  0.4 -clock clk -add_delay [get_ports C]
set_output_delay 0.8 -clock clk -add_delay [get_ports D]

# 另一种等价写法：直接限制 C -> D 的组合逻辑延时
# set_max_delay 1.2 -from [get_ports C] -to [get_ports D]
