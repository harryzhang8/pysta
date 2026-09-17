# ==========================================================================
# 例 8：时序例外 —— 逻辑伪路径
#
# 两个 MUX 共用同一个选择信号，交叉通路永远不会导通，
# 但它们在网表里确实连着 —— 不做约束的话工具有可能去优化这两条假路径。
#
# 三个筛选参数的语义：
#     -from     起始点
#     -through  路径必须穿过这个点（写多次时是「且」的关系，且有顺序要求）
#     -to       终点
#
# 所以下面两条分别排除两种交叉通路：
#     set_false_path -from [get_ports A] -through [get_pins MUX0/I0] -through [get_pins MUX1/I1] -to [get_ports B]
#     set_false_path -from [get_ports A] -through [get_pins MUX0/I1] -through [get_pins MUX1/I0] -to [get_ports B]
#
# 如果不在乎起点终点，也可以只靠 -through（文件末尾注释的两条），
# 语义是“只要穿过这两个引脚的路径都是伪路径”。
#
# 运行效果：
#   同侧通路（MUX0/I0 -> MUX1/I0 等）仍然正常分析；
#   交叉通路被标为 EXCLUDED。
#
# 另外补了 input/output delay，让剩下的真实通路有 slack 可看。
# ==========================================================================

create_clock -period 10 [get_ports clk]

set_input_delay  -max 2.0 -clock clk [get_ports A]
set_output_delay -max 3.0 -clock clk [get_ports B]

set_false_path -from [get_ports A] -through [get_pins MUX0/I0] -through [get_pins MUX1/I1] -to [get_ports B]
set_false_path -from [get_ports A] -through [get_pins MUX0/I1] -through [get_pins MUX1/I0] -to [get_ports B]

# 等效写法：不限定起点终点，只要穿过这两个引脚就算伪路径
# set_false_path -through [get_pins MUX0/I0] -through [get_pins MUX1/I1]
# set_false_path -through [get_pins MUX0/I1] -through [get_pins MUX1/I0]
