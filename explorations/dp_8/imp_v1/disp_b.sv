// -----------------------------------------------------------------------------
// Author: Simone Machetti
//
// Description:
//   imp_v1 disp_b: the column dispatcher's part of the DP8. Passes the B operands through.
// -----------------------------------------------------------------------------

`timescale 1 ns/1 ps

module disp_b (
    input  logic [3:0] b_i [0:7],
    output logic [3:0] b_o [0:7]
);

    assign b_o = b_i;

endmodule
