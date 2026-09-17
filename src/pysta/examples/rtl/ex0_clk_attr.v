//==========================================================================
// 例 0（RTL）：时钟属性与对应约束
//--------------------------------------------------------------------------
// RTL 里只写一个普通的同步逻辑。真正体现"时钟属性建模"的是 SDC：
//     skew + jitter  -> set_clock_uncertainty
//     transition     -> set_clock_transition
//     latency        -> set_clock_latency -source / set_clock_latency
//                       布线后改用 set_propagated_clock
//
// 注意：门级网表里那个 CLKBUF 是综合/布局布线阶段插进去的时钟树缓冲器，
//       RTL 层面看不到它 —— 这也正是"网络延迟在布线前只能靠约束估计"的原因。
//==========================================================================
module ex0_clk_attr (
  input  wire clk,
  input  wire d,
  output wire q
);

  reg q1;
  reg q2;

  always @(posedge clk) begin
    q1 <= d;
    q2 <= ~q1;
  end

  assign q = q2;

endmodule
