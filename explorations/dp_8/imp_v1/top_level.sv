// -----------------------------------------------------------------------------
// Author: Simone Machetti
//
// Description:
//   imp_v1 top_level: one DP8 of the grid between an input and an output register. a_i and
//   b_i are registered, go through the row dispatcher's part (disp_a), the column
//   dispatcher's part (disp_b) and the DP8 in the PE (dp_8), and y_o is registered: two
//   cycles of latency.
//
//   The ports are the fixed interface (tb_top_level.sv): y_o = sum_k a_i[k] * b_i[k], with
//   a_i[k] signed 8-bit and b_i[k] signed 4-bit; the value lies in [-8128, +8192], a signed
//   15-bit number. The registers are fixed too; between them everything is the
//   implementation's: here the buses carry the operands unchanged. There is no register
//   between the dispatchers and the PE: disp -> dp_8 is one combinational path.
// -----------------------------------------------------------------------------

`timescale 1 ns/1 ps

module top_level (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic [ 7:0] a_i [0:7],
    input  logic [ 3:0] b_i [0:7],
    output logic [14:0] y_o
);

    logic [ 7:0] a_q   [0:7];
    logic [ 3:0] b_q   [0:7];
    logic [ 7:0] a_bus [0:7];
    logic [ 3:0] b_bus [0:7];
    logic [14:0] y;

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            for (int k = 0; k < 8; k++) begin
                a_q[k] <= '0;
                b_q[k] <= '0;
            end
            y_o <= '0;
        end else begin
            for (int k = 0; k < 8; k++) begin
                a_q[k] <= a_i[k];
                b_q[k] <= b_i[k];
            end
            y_o <= y;
        end
    end

    disp_a u_disp_a (
        .a_i(a_q),
        .a_o(a_bus)
    );

    disp_b u_disp_b (
        .b_i(b_q),
        .b_o(b_bus)
    );

    dp_8 u_dp_8 (
        .a_i(a_bus),
        .b_i(b_bus),
        .y_o(y)
    );

endmodule
