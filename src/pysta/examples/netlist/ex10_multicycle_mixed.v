//==========================================================================
// 例 10：多周期路径与单周期路径共存
//--------------------------------------------------------------------------
// 设计规格：寄存器之间的**乘法运算为两个时钟周期**，
//           **加法运算为默认的一个时钟周期**。
//
// 电路里有两条从 FF1 出发的路径：
//   ① FF1 -> Multiply -> Add -> FF2/D   （2 个周期）
//   ② FF1 -> XOR      -> FF3/D          （默认 1 个周期）
//
// 约束：
//     set_multicycle_path -setup 2 -from FF1/CP -through Multiply/out -to FF2/D
//     set_multicycle_path -hold  1 -from FF1/CP -through Multiply/out -to FF2/D
// 注意 -through 把多周期约束**只**加在乘法那条路径上，
// 第二条普通路径仍然按 1 个周期检查。
//==========================================================================
module ex10_multicycle_mixed (
  clk,
  in_a,
  in_b,
  out_y,
  out_z
);

  input  clk;
  input  in_a;
  input  in_b;
  output out_y;    // 多周期路径的结果（乘法）
  output out_z;    // 普通路径的结果

  wire q1;
  wire prod;
  wire sum;
  wire cout;
  wire par;

  DFFRQ   FF1      (.CP(clk), .D(in_a), .Q(q1));

  // ① 多周期（2 个时钟周期）路径：乘法器 -> 加法器
  MULT8X8 Multiply (.A(q1),   .B(in_b), .out(prod));
  ADDFA   Add      (.A(prod), .B(prod), .CI(1'b0), .S(sum), .CO(cout));
  DFFRQ   FF2      (.CP(clk), .D(sum),  .Q(out_y));

  // ② 普通（1 个时钟周期）路径
  XOR2X1  U1       (.A(q1),   .B(in_b), .Y(par));
  DFFRQ   FF3      (.CP(clk), .D(par),  .Q(out_z));

endmodule
