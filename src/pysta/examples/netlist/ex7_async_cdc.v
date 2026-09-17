//==========================================================================
// 例 7：时序例外 —— 异步路径
//--------------------------------------------------------------------------
// 场景：
//   clka 来自晶振 1（50MHz，周期 20ns），clkb 来自晶振 2（100MHz，周期 10ns）。
//   两个时钟来自完全独立的时钟源，相位关系不确定，所以跨越 clka/clkb 的
//   路径**不应该**做时序分析 —— 相位不确定的路径做分析没有意义。
//
// 约束方式：
//     set_false_path -from [get_clocks clka] -to [get_clocks clkb]
//     set_false_path -from [get_clocks clkb] -to [get_clocks clka]
//   或者用一条 set_clock_groups -asynchronous 代替。
//
// 加上 false path 之后，工具会停止对这几条路径做时序分析和优化。
// 本例把两个方向的跨时钟域路径都做出来，方便观察前后的差别。
//
// （提醒：false_path 只是让工具别去优化它，并不代表电路本身安全 ——
//   真实设计里跨时钟域还需要同步器或握手逻辑。）
//==========================================================================
module ex7_async_cdc (
  clka,
  clkb,
  DA,
  DB,
  QA,
  QB
);

  input  clka;     // 晶振 1：50MHz
  input  clkb;     // 晶振 2：100MHz
  input  DA;
  input  DB;
  output QA;
  output QB;

  wire qa;         // clka 域寄存器输出
  wire qb;         // clkb 域寄存器输出
  wire na;
  wire nb;

  // --- clka 域 ---
  DFFRQ FF_A1 (.CP(clka), .D(DA), .Q(qa));
  DFFRQ FF_A2 (.CP(clka), .D(nb), .Q(QA));

  // --- clkb 域 ---
  DFFRQ FF_B1 (.CP(clkb), .D(DB), .Q(qb));
  DFFRQ FF_B2 (.CP(clkb), .D(na), .Q(QB));

  // --- 两个方向的跨时钟域组合逻辑 ---
  INVX1  U1 (.A(qa), .Y(na));      // clka -> clkb
  BUFX1  U2 (.A(qb), .Y(nb));      // clkb -> clka

endmodule
