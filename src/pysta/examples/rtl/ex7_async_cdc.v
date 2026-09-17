//==========================================================================
// 例 7（RTL）：时序例外 —— 异步路径
//--------------------------------------------------------------------------
// 两个完全独立的时钟域（来自两颗晶振），互相之间有数据穿越。
//
// 预期结果：跨时钟域路径要用 set_false_path 解除约束 ——
//           不同时钟源的相位关系一直在变，对它们做时序分析没有意义。
//           （写代码的时候别忘了这个电路本身还需要同步器/握手！
//             false_path 只是让工具别去优化它，不代表电路是安全的。）
//==========================================================================
module ex7_async_cdc (
  input  wire clka,     // 晶振 1：50MHz
  input  wire clkb,     // 晶振 2：100MHz
  input  wire DA,
  input  wire DB,
  output wire QA,
  output wire QB
);

  reg qa;
  reg qb;

  always @(posedge clka) qa <= DA;
  always @(posedge clkb) qb <= DB;

  // --- clka 域 -> clkb 域 ---
  // 真实设计里这里应该是两级同步器；本示例只关心时序约束，
  // 所以原样保留一级组合逻辑，让跨域路径显式存在。
  wire na = ~qa;

  reg qb2;
  always @(posedge clkb) qb2 <= na;

  // --- clkb 域 -> clka 域 ---
  wire nb = qb;
  reg qa2;
  always @(posedge clka) qa2 <= nb;

  assign QA = qa2;
  assign QB = qb2;

  // 说明：门级网表 netlist/ex7_async_cdc.v 里把上面的连接直接写成了
  // FF_A1 -> INV -> FF_B2 与 FF_B1 -> BUF -> FF_A2 两条跨域路径。
endmodule
