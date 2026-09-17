//==========================================================================
// 例 8（RTL）：时序例外 —— 逻辑伪路径
//--------------------------------------------------------------------------
// 两个共用同一个选择信号 S 的 MUX 串接：
//     S=0 时通路是 MUX0/I0 -> MUX1/I0
//     S=1 时通路是 MUX0/I1 -> MUX1/I1
// 交叉通路（I0 -> I1 / I1 -> I0）物理上连着、逻辑上永远不会导通。
//
// 预期用的约束：
//   set_false_path -from [get_ports A] -through [get_pins MUX0/I0] \
//                  -through [get_pins MUX1/I1] -to [get_ports B]
//   多个 -through 之间是「且」的关系。
//==========================================================================
module ex8_pseudo_path (
  input  wire clk,
  input  wire A,
  input  wire S,
  output wire B
);

  // 注意：这里写成两级 MUX 串联，func 展开后交叉项会被综合优化掉吗？
  // 不会 —— 因为两个 MUX 共用 S，交叉通路在结构上确实存在，
  // 只是逻辑上不可能同时导通。这正是"伪路径"的定义。
  wire y0 = S ? A : A;      // MUX0（I0 = I1 = A）
  wire y1 = S ? y0 : y0;    // MUX1（I0 = I1 = y0）
  wire b  = y1;

  assign B = b;

endmodule
