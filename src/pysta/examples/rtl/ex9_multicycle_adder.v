//==========================================================================
// 例 9（RTL）：多周期路径 —— 加法器
//--------------------------------------------------------------------------
// 多周期路径要成立，需要两件事同时到位：
//
//  1) 时序约束（SDC 里的事）：
//         set_multicycle_path -setup 6 -to [get_pins FF3[*]/D]
//         set_multicycle_path -hold  5 -to [get_pins FF3[*]/D]
//     这只是告诉工具"这条路径允许跑 6 个周期"。
//
//  2) RTL 设计（本文件里的事）：
//     约束只保证时序上允许 6 个周期，**并不能**保证下游寄存器在第几个
//     周期去采样。如果每个周期都在写结果寄存器，采到的就是中间态。
//     所以 RTL 里必须自己产生"每 6 拍采一次"的使能信号 ——
//     下面用 6 位移位寄存器做 one-hot 轮转来实现。
//
// 所以下面给出**两个版本**：
//     ex9_multicycle_adder_naive   —— 只加约束、每个周期都写（会出错）
//     ex9_multicycle_adder         —— 约束 + 6 拍循环移位使能（正确）
//==========================================================================
module ex9_multicycle_adder_naive (
  input  wire clk,
  input  wire A0,
  input  wire A1,
  input  wire B0,
  input  wire B1,
  output wire S0,
  output wire S1
);

  reg a0, a1, b0, b1;
  reg s0, s1;

  always @(posedge clk) begin
    a0 <= A0;  a1 <= A1;
    b0 <= B0;  b1 <= B1;
    // 加法器延时约 6 个周期，可是这里每个周期都在写 s0/s1，
    // 综合出来的电路会被采到中间态 —— 这是**错误**的写法。
    {s1, s0} <= {1'b0, a0} + {1'b0, b0} + {1'b0, a1} + {1'b0, b1};
  end

  assign S0 = s0;
  assign S1 = s1;

endmodule


//==========================================================================
// 正确版本：用 6 位循环移位寄存器产生"每 6 拍更新一次"的使能
//==========================================================================
module ex9_multicycle_adder (
  input  wire clk,
  input  wire rst_n,
  input  wire A0,
  input  wire A1,
  input  wire B0,
  input  wire B1,
  output wire S0,
  output wire S1
);

  // ---- 6 拍循环移位（one-hot 轮转）----
  reg [5:0] phase;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) phase <= 6'b000001;
    else        phase <= {phase[4:0], phase[5]};
  end

  wire load_en  = phase[0];   // 第 0 拍：锁存操作数
  wire capture_en = phase[5]; // 第 6 拍：采样加法结果

  reg a0, a1, b0, b1;
  always @(posedge clk) begin
    if (load_en) begin
      a0 <= A0;  a1 <= A1;
      b0 <= B0;  b1 <= B1;
    end
  end

  // 组合加法器：它的延时决定了一条"多周期路径"
  wire [1:0] sum = {1'b0, a0} + {1'b0, a1} + {1'b0, b0} + {1'b0, b1};

  reg s0, s1;
  always @(posedge clk) begin
    if (capture_en) begin
      s0 <= sum[0];
      s1 <= sum[1];
    end
  end

  assign S0 = s0;
  assign S1 = s1;

endmodule
