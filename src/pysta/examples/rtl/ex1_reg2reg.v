//==========================================================================
// 例 1（RTL）：寄存器 -> 寄存器
//--------------------------------------------------------------------------
// 这是设计意图层面的写法。综合之后得到 netlist/ex1_reg2reg.v 那张门级网表，
// STA 真正分析的是门级网表，这里只是让人看清电路在做什么。
//
// 预期结果：TN <= Tclk - Tco - Tsu - Tuncertainty
//==========================================================================
module ex1_reg2reg (
  input  wire clk,
  input  wire dina,
  output wire douta
);

  reg q1;
  reg q2;

  // 逻辑 N：~(~q1 & dina)
  wire n1 = ~q1;

  always @(posedge clk) begin
    q1 <= dina;
    q2 <= ~(n1 & dina);
  end

  assign douta = q2;

endmodule
