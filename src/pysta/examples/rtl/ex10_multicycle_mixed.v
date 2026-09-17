//==========================================================================
// 例 10（RTL）：多周期路径与单周期路径共存
//--------------------------------------------------------------------------
// 同一对寄存器之间，不同运算路径的周期数不同：
//     乘法路径：2 个时钟周期
//     异或路径：1 个时钟周期（默认）
//
// 预期用的约束：
//     set_multicycle_path -setup 2 -from FF1/CP -through Multiply/out -to FF2/D
//     set_multicycle_path -hold  1 -from FF1/CP -through Multiply/out -to FF2/D
//   -through 让多周期约束只作用在乘法那条路径上。
//==========================================================================
module ex10_multicycle_mixed (
  input  wire clk,
  input  wire in_a,
  input  wire in_b,
  output wire out_y,
  output wire out_z
);

  reg q1;

  always @(posedge clk) begin
    q1 <= in_a;
  end

  // ① 多周期（2 周期）路径：乘法 -> 加法
  wire         prod = q1 * in_b;     // 这里用 1bit 示意；门级网表里是 8x8 宏单元
  wire [1:0]   sum  = {1'b0, prod} + {1'b0, prod} + {1'b0, in_b};

  reg y;
  always @(posedge clk) y <= sum[0];

  // ② 普通（1 周期）路径：异或
  reg z;
  always @(posedge clk) z <= q1 ^ in_b;

  assign out_y = y;
  assign out_z = z;

endmodule
