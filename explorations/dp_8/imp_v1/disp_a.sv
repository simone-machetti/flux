// -----------------------------------------------------------------------------
// Author: Simone Machetti
//
// Description:
//   imp_v1 disp_a: the row dispatcher's part of the DP8. Passes the A operands through.
// -----------------------------------------------------------------------------

`timescale 1 ns/1 ps

module disp_a (
    input  logic [7:0] a_i [0:7],
    output logic [7:0] a_o [0:7]
);

    assign a_o = a_i;

endmodule
