// -----------------------------------------------------------------------------
// Author: Simone Machetti
//
// Description:
//   Self-checking testbench for top_level, fixed for the whole exploration (the golden
//   model). After a reset it applies one vector per clock cycle (1 ns): directed corners,
//   then NUM_RAND random vectors, and checks each result LATENCY cycles later:
//     y_o == sum_i(a_i[i] * b_i[i])      a_i[i] signed 8-bit, b_i[i] signed 4-bit
//   Lanes are biased toward the extreme values so the corners of the sum are hit. Stops at
//   the first mismatch and prints `N failing of M` for eval.py. Runs on the RTL (Verilator)
//   and on the synthesized netlist (Icarus, +define+GLS: synthesis flattens the unpacked
//   ports, element 0 at the top); +define+VCD dumps activity.vcd.
// -----------------------------------------------------------------------------

`timescale 1 ns/1 ps

module tb_top_level;

    localparam int NUM_RAND = 4000;
    localparam int LATENCY  = 2;

    localparam logic [7:0] A_MAX_POS = 8'h7F;
    localparam logic [7:0] A_MIN_NEG = 8'h80;
    localparam logic [3:0] B_MAX_POS = 4'h7;
    localparam logic [3:0] B_MIN_NEG = 4'h8;

    logic        clk;
    logic        rst_n;
    logic [ 7:0] a      [0:7];
    logic [ 3:0] b      [0:7];
    logic [14:0] y;
    logic [ 7:0] a_hist [0:LATENCY-1][0:7];
    logic [ 3:0] b_hist [0:LATENCY-1][0:7];
    int          exp_hist [0:LATENCY-1];
    int          applied;
    int          count;

    initial clk = 1'b0;
    always #0.5 clk = ~clk;

`ifdef GLS
    logic [63:0] a_flat;
    logic [31:0] b_flat;

    genvar g;
    generate
        for (g = 0; g < 8; g++) begin : gen_flat
            assign a_flat[(7-g)*8 +: 8] = a[g];
            assign b_flat[(7-g)*4 +: 4] = b[g];
        end
    endgenerate

    top_level dut (
        .clk_i (clk),
        .rst_ni(rst_n),
        .a_i   (a_flat),
        .b_i   (b_flat),
        .y_o   (y)
    );
`else
    top_level dut (
        .clk_i (clk),
        .rst_ni(rst_n),
        .a_i   (a),
        .b_i   (b),
        .y_o   (y)
    );
`endif

    // Called at a falling edge with the next vector on a and b: checks the result of the
    // vector applied LATENCY cycles ago, then applies this one for a cycle.
    task automatic apply;
        int exp, got, a_val, b_val;
        if (applied >= LATENCY) begin
            got   = $signed(y);
            count = count + 1;
            if (got !== exp_hist[LATENCY-1]) begin
                $write("MISMATCH exp=%0d got=%0d:", exp_hist[LATENCY-1], got);
                for (int i = 0; i < 8; i++) begin
                    $write(" a[%0d]=0x%02h b[%0d]=0x%01h", i, a_hist[LATENCY-1][i], i, b_hist[LATENCY-1][i]);
                end
                $display("");
                $display("1 failing of %0d", count);
                $finish;
            end
        end
        exp = 0;
        for (int i = 0; i < 8; i++) begin
            a_val = a[i];
            b_val = b[i];
            if (a_val > 127) a_val = a_val - 256;
            if (b_val > 7)   b_val = b_val - 16;
            exp = exp + a_val * b_val;
        end
        for (int s = LATENCY-1; s > 0; s--) begin
            exp_hist[s] = exp_hist[s-1];
            for (int i = 0; i < 8; i++) begin
                a_hist[s][i] = a_hist[s-1][i];
                b_hist[s][i] = b_hist[s-1][i];
            end
        end
        exp_hist[0] = exp;
        for (int i = 0; i < 8; i++) begin
            a_hist[0][i] = a[i];
            b_hist[0][i] = b[i];
        end
        applied = applied + 1;
        @(negedge clk);
    endtask

    task automatic rand_vec;
        int pa, pb;
        for (int i = 0; i < 8; i++) begin
            pa = {$random} % 5;
            pb = {$random} % 5;
            a[i] = (pa == 0) ? A_MIN_NEG : (pa == 1) ? A_MAX_POS : $random;
            b[i] = (pb == 0) ? B_MIN_NEG : (pb == 1) ? B_MAX_POS : $random;
        end
    endtask

    task automatic set_vec(input logic [7:0] av, input logic [3:0] bv);
        for (int i = 0; i < 8; i++) begin
            a[i] = av;
            b[i] = bv;
        end
    endtask

    initial begin
`ifdef VCD
        $dumpfile("activity.vcd");
        $dumpvars(0, dut);
`endif

        applied = 0;
        count   = 0;
        rst_n   = 1'b0;
        set_vec(8'h00, 4'h0);
        repeat (2) @(negedge clk);
        rst_n   = 1'b1;

        set_vec(8'h00, 4'h0);          apply;
        set_vec(A_MAX_POS, B_MAX_POS); apply;
        set_vec(A_MIN_NEG, B_MIN_NEG); apply;
        set_vec(A_MAX_POS, B_MIN_NEG); apply;
        set_vec(A_MIN_NEG, B_MAX_POS); apply;
        set_vec(8'hFF, B_MIN_NEG);     apply;
        set_vec(8'hFF, 4'hF);          apply;
        for (int t = 0; t < NUM_RAND; t++) begin
            rand_vec;
            apply;
        end
        for (int t = 0; t < LATENCY; t++) begin
            set_vec(8'h00, 4'h0);
            apply;
        end

        $display("0 failing of %0d", count);
        $finish;
    end

endmodule
