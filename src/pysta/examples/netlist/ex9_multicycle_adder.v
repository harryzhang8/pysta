//==========================================================================
// 例 9：多周期路径 —— 加法器
//--------------------------------------------------------------------------
// 电路模型：时钟周期 10ns，按设计规格加法器的延时约为 6 个时钟周期。
//   操作数寄存器打一拍 -> 加法器 -> 结果寄存器数组 FF3[*]
//
// 关键结论：
//   默认建立时间约束：在第 1 个上升沿(10ns)就检查 -> 必然违例
//   set_multicycle_path -setup 6 -to [get_pins FF3[*]/D]
//       -> 推迟到第 6 个上升沿(60ns)检查
//       -> 加法器允许的最大延时 = 60 - Tco - Tsu - Tuncertainty
//
//   但此时**保持时间检查会自动跟着挪到 50ns**（建立沿的前一个沿），
//   要求加法器最小延时 >= 50 + Th + Tuncertainty - Tco —— 荒谬！
//   所以必须补一条： set_multicycle_path -hold 5 -to [get_pins FF3[*]/D]
//       -> 把保持检查从 50ns 拉回 0ns
//
// 本例保留 FF3 的数组命名（FF3[0] / FF3[1]），
// 以便 SDC 里能直接写 get_pins FF3[*]/D 这种数组匹配。
//==========================================================================
module ex9_multicycle_adder (
  clk,
  A0,
  A1,
  B0,
  B1,
  S0,
  S1
);

  input  clk;
  input  A0;       // 操作数 A 的 bit0（经 FF1 打拍）
  input  A1;       // 操作数 A 的 bit1（经 FF4 打拍）
  input  B0;       // 操作数 B 的 bit0（经 FF2 打拍）
  input  B1;       // 操作数 B 的 bit1（经 FF5 打拍）
  output S0;       // 和的 bit0（FF3[0] 输出）
  output S1;       // 和的 bit1（FF3[1] 输出）

  wire qa0;
  wire qa1;
  wire qb0;
  wire qb1;
  wire c0;
  wire sum0;
  wire sum1;
  wire cout;

  // ---- 操作数寄存器 ----
  DFFRQ FF1 (.CP(clk), .D(A0), .Q(qa0));
  DFFRQ FF4 (.CP(clk), .D(A1), .Q(qa1));
  DFFRQ FF2 (.CP(clk), .D(B0), .Q(qb0));
  DFFRQ FF5 (.CP(clk), .D(B1), .Q(qb1));

  // ---- 加法器（约 6 个时钟周期）----
  ADDFA ADD0 (.A(qa0), .B(qb0), .CI(1'b0), .S(sum0), .CO(c0));
  ADDFA ADD1 (.A(qa1), .B(qb1), .CI(c0),   .S(sum1), .CO(cout));

  // ---- 结果寄存器数组（多周期路径的终点）----
  DFFRQ FF3[0] (.CP(clk), .D(sum0), .Q(S0));
  DFFRQ FF3[1] (.CP(clk), .D(sum1), .Q(S1));

endmodule
