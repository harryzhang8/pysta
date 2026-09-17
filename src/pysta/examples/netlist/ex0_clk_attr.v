//==========================================================================
// 例 0：时钟属性与对应约束
//--------------------------------------------------------------------------
// 把时钟拆成四个属性来建模：
//   1. 时钟偏移 (skew)        —— 到达各寄存器的相位差
//   2. 时钟抖动 (jitter)      —— 相对理想沿的超前/滞后
//       这两项在 DC 里统一用 set_clock_uncertainty 表示
//   3. 时钟转换时间 (transition) —— 20%~80% 的跳变时间，用 set_clock_transition
//   4. 时钟延时 (latency)     —— 分两类：
//        source latency  (插入延迟)：时钟源 -> 芯片时钟端口，例如 3ns
//        network latency (网络延迟)：时钟端口 -> 寄存器 CP 引脚，例如 1ns
//        DC 里用 set_clock_latency -source / set_clock_latency
//        布线之后网络延迟可以由实际时钟树算出来，
//        这时用 set_propagated_clock 取代 set_clock_latency
//
// 本例在时钟路径上放了一个 CLKBUF，模拟"时钟树上的缓冲器"，
// 让"网络延迟"这个概念有个看得见的载体。
//==========================================================================
module ex0_clk_attr (
  clk,
  d,
  q
);

  input  clk;
  input  d;
  output q;

  wire ck;         // 经过时钟树缓冲器之后的时钟
  wire q1;
  wire n1;

  // 时钟树上的缓冲器
  CLKBUF CKB (.A(clk), .Y(ck));

  DFFRQ  FF1 (.CP(ck), .D(d),  .Q(q1));
  INVX1  U1  (.A(q1),  .Y(n1));
  DFFRQ  FF2 (.CP(ck), .D(n1), .Q(q));

endmodule
