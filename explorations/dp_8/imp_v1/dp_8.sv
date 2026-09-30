// -----------------------------------------------------------------------------
// Author: Simone Machetti
//
// Description:
//   imp_v1 dp_8: the DP8 in the PE, behavioral: eight signed products with the SV `*`,
//   accumulated by a chain of adders.
// -----------------------------------------------------------------------------

`timescale 1 ns/1 ps

module dp_8 (
    input  logic [ 7:0] a_i [0:7],
    input  logic [ 3:0] b_i [0:7],
    output logic [14:0] y_o
);

    logic signed [14:0] acc;

    always_comb begin
        acc = '0;
        for (int k = 0; k < 8; k++) begin
            acc = acc + $signed(a_i[k]) * $signed(b_i[k]);
        end
    end

    assign y_o = acc;

endmodule
