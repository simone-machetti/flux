# dp_8 champion: dp_8#405, design "S2"

Transcribed from 27 photos of the run's "THE DESIGN" panel (2026-10-06). The code and
comments are copied as shown; whitespace and column alignment are approximate. The one
passage that is not visible in any photo is marked `[...]` and filled in from the code.

## Results (postsyn)

| fmax_ratio | area_ratio | power_ratio | ap_ratio |
|-----------:|-----------:|------------:|---------:|
|      1.229 |     0.3056 |      0.3757 |   0.1148 |

The photos show the four numbers without column names. The names come from the `postsyn`
metric order in `dp_8.problem.yaml`, and they check out: 0.3056 x 0.3757 = 0.1148 (ap_ratio),
and 1.229 is the `fmax_ratio 1.22862` quoted in top_level's header. Relative to imp_v1, the
design is about 23% faster at roughly 31% of the area and 38% of the power.

## How the design works

- **disp_b** (per column, costed at 1/8): recodes each signed int4 `b` into two radix-4 Booth
  digits `d0, d1` in {-2..2}. It sends each digit as `{neg, mag==2, mag==1}`, giving a 6-bit
  lane, plus one shared 6-bit `c_o = popcount(b1) + 4*popcount(b3)`. That value holds all the
  "+1"s needed to complete the two's-complement negations of the 8 PEs.
- **disp_a** (per row): passes `a` through unchanged.
- **dp_8** (per PE): for each lane, picks `X = {0, a, 2a}` (9 bits), XORs it with `neg`, and
  builds two narrow rows: 9 bits (even row) and 11 bits (odd row, shifted by 2). These rows have
  no sign extension. Instead, each row's sign bit is inverted, so every row adds a fixed bias
  (+256 even, +1024 odd) whatever its sign. The total bias is 8*256 + 8*1024 = 10240, which is
  cancelled by one constant row `c_row` = 22528 (= -10240 mod 2^15, bits 14/12/11) with `c_i`
  in bits 5..0.
- The 17 rows (16 partial products + `c_row`) are reduced by a 6-level 3:2 carry-save tree,
  17 -> 12 -> 9 -> 6 -> 4 -> 3 -> 2, then summed by one 15-bit add:

| Level | CSAs (inputs -> s, co)                                                                                               | Passed through                       |
|-------|----------------------------------------------------------------------------------------------------------------------|--------------------------------------|
| 0     | u00 (r0,r2,r4), u01 (r6,r8,r10), u02 (r1,r3,r5), u03 (r7,r9,r11), u04 (r12,r13,r14) -> t0[0..9]                       | t0[10]=r[15], t0[11]=c_row            |
| 1     | u10 (t0[0],t0[2],t0[8]), u11 (t0[1],t0[3],t0[9]), u12 (t0[4],t0[6],t0[10]) -> t1[0..5]                               | t1[6]=t0[5], t1[7]=t0[7]; c_row waits |
| 2     | u20 (t1[0],t1[1],t1[2]), u21 (t1[3],t1[4],c_row), u22 (t1[5],t1[6],t1[7]) -> t2[0..5]                                 |                                      |
| 3     | u30 (t2[0],t2[1],t2[2]), u31 (t2[3],t2[4],t2[5]) -> t3[0..3]                                                         |                                      |
| 4     | u40 (t3[0],t3[1],t3[2]) -> t4[0..1]                                                                                  | t4[2]=t3[3]                          |
| 5     | u50 (t4[0],t4[1],t4[2]) -> t5[0..1]                                                                                  | y = t5[0] + t5[1]                    |

### Hand checks of the header's claims (no tools run)

- `d0 + 4*d1 = (-2*b1 + b0) + 4*(-2*b3 + b2 + b1) = b0 + 2*b1 + 4*b2 - 8*b3 = b`.
- The `d_o` bit equations match `{neg1, m1_1, m1_0, neg0, m0_1, m0_0}`: `m0_0 = b0`,
  `m0_1 = b1 & ~b0`, `neg0 = b1`, `m1_0 = b1 ^ b2`, `m1_1 = (b2 & b1 & ~b3) | (~b2 & ~b1 & b3)`,
  `neg1 = b3`.
- Even row: with neg=0 it equals `x + 256`. With neg=1 it equals `-x - 1 + 256`, and the `+1`
  comes from `c_i`. The odd row is the same, scaled by 4: `4*a*d1 + 1024`.
- -10240 mod 32768 = 22528 = 2^14 + 2^12 + 2^11, which matches `{1'b1, 1'b0, 1'b1, 1'b1, 5'b0, c_i}`.
- `c_o` is at most 8 + 4*8 = 40, which fits in 6 bits. `s1 + (s3 << 2)` is evaluated at the 6-bit width of `c_o`,
  so the shift doesn't overflow.
- `y` lies in [-8128, 8192], which fits a signed 15-bit result, so working mod 2^15 is exact.

### Things noticed in the code (all harmless, some may raise lint warnings)

- `r` is declared `[0:16]`, but `r[16]` is never driven or read: the 17th row is `c_row`.
- `t0[11] = c_row` is assigned but never read, because u21 takes `c_row` directly.
- In `csa3`, for `i == 0` the untaken branch of `?:` still contains `a[i-1]`, a constant index of -1.
  The result is correct, but Verilator may report it as an out-of-range select.
- In `dp_8`, `s0`/`s1` are packed `logic [7:0]` vectors indexed per lane. All other per-lane
  signals are unpacked arrays.
- The header comment is repeated before every section (`eval.py` puts the file's header
  comment in front of each part). The copy before `dp_8.sv` has different last three lines
  (shown below). The photos don't show why.

## Header comment (shown before each section)

```systemverilog
// dp_8: signed int8 x signed int4 eight-term dot product, signed 15-bit output.
//
// Structure (design "S2": Booth radix-4, NARROW partial products with INVERTED
// sign bits -> CONSTANT over-contribution, single merged constant row, no
// popcount, global carry-save tree):
//   y = sum_k a_k*b_k with b_k = d0_k + 4*d1_k, Booth digits in {-2,-1,0,1,2}.
//   disp_b recodes every b_k into {neg1, m1_1, m1_0, neg0, m0_1, m0_0} (6-bit
//   lane) and precomputes the shared negation constant c_o = popcount(b1) +
//   4*popcount(b3) (6 bits): both are b-only work shared by the 8 PEs of the
//   column (1/8 cost).  disp_a passes the A operands through; the 2a shifts are
//   pure wiring.
//   dp_8 forms X = mux{0,a,2a} (9 bits, sign bit x[8] = (m0|m1)&a[7]) and the
//   two narrow partial-product rows WITHOUT sign extension but with INVERTED
//   sign bits:
//       r[2k]   = {~s0, x0[7:0]^neg0}             sign bit ~s0 at column 8
//       r[2k+1] = {~s1, x1[7:0]^neg1, 2'b0}       sign bit ~s1 at column 10
//   where s0 = x0[8]^neg0, s1 = x1[8]^neg1.  Inverting the sign bit makes every
//   row over-contribute a CONSTANT instead of a sign-dependent amount: even rows
//   add +256 and odd rows add +1024 (verified: r0 = a*d0 + 256, r1 = 4*a*d1 +
//   1024 in both the neg=0 and neg=1 cases, the negations' +1 still coming from
//   c_o).  The 8 lanes over-contribute 8*256 + 8*1024 = 10240, so the whole
//   correction is the single constant row K = -10240 mod 2^15 = 22528, bits 14,
//   12 and 11, merged with c_i at columns 0..5:
//       c_row = {1'b1, 1'b0, 1'b1, 1'b1, 5'b0, c_i}      (cols 0..5, 11, 12, 14)
//   No popcount of the sign bits, no q, no corr6, no corr_row: the a-dependent
//   correction logic that was on dp_8's critical path is gone.  The 16 partial-
//   product rows and the one constant row (17 rows) are compressed by a balanced
//   3:2 carry-save tree over columns 0..14 (arithmetic mod 2^15, exact because
//   |y| <= 8192 < 2^14, carry out of column 14 is dropped) and finished by one
//   plain 15-bit add.  c_row is b-only and arrives early, but its high bits
//   (11, 12, 14) stay out of the lower tree: it enters at stage 2 with
//   t1[3]/t1[4] (r[15] bypasses via t0[10], t0[11]=c_row rides to stage 2).
```

In the copy before `// file: dp_8.sv`, the last three lines read instead:

```systemverilog
//   plain 15-bit add.  c_row is b-only and arrives early; the stage-0 groups are
//   (0,2,4) (6,8,10) (1,3,5) (7,9,11) (12,13,14) (bypass r[15]) and stage-1 groups [...]
//   t1[7]=t0[7]) so every csa3 at a level mixes rows with aligned spans.
```

`[...]` is cut off in every photo. Judging by the code, it most likely lists `(0,2,8) (1,3,9) (4,6,10) (bypass t1[6]=t0[5],`.

## top_level.sv

```systemverilog
// file: top_level.sv
// ---------------------------------------------------------------------------
// top_level: one DP8 of the grid between an input and an output register. a_i and b_i
// are registered, go through disp_a, disp_b and dp_8, and y_o is registered: two
// cycles of latency. Only the three blocks and the fixed registers, nothing else.
// Pass-20 re-seat: comment-diff of the stg1opt champion (dp_8#405, fmax_ratio 1.22862).
// ---------------------------------------------------------------------------

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
    logic [ 5:0] b_bus [0:7];
    logic [ 5:0] c_bus;
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
        .d_o(b_bus),
        .c_o(c_bus)
    );

    dp_8 u_dp_8 (
        .a_i(a_bus),
        .b_i(b_bus),
        .c_i(c_bus),
        .y_o(y)
    );

endmodule
```

## disp_a.sv

```systemverilog
// file: disp_a.sv
// ---------------------------------------------------------------------------
// disp_a: passes the A operands through unchanged.
// ---------------------------------------------------------------------------

`timescale 1 ns/1 ps

module disp_a (
    input  logic [7:0] a_i [0:7],
    output logic [7:0] a_o [0:7]
);

    assign a_o = a_i;

endmodule
```

## disp_b.sv

```systemverilog
// file: disp_b.sv
// ---------------------------------------------------------------------------
// disp_b: radix-4 Booth recoding of every b_k plus the shared negation constant.
//   For b = {b3,b2,b1,b0} (signed 4-bit): b = d0 + 4*d1 with
//     d0 = -2*b1 + b0 and d1 = -2*b3 + b2 + b1, each digit in {-2,-1,0,1,2}.
//   Each digit is sent as {neg, mag1, mag0} (mag = |digit| in {0,1,2}):
//     d_o[k] = {neg1, m1_1, m1_0, neg0, m0_1, m0_0}
//   and c_o = sum_k neg0_k + 4*sum_k neg1_k = popcount(b1) + 4*popcount(b3),
//   the carry-in that completes every negation in the PE (value in [0,40]).
// ---------------------------------------------------------------------------

`timescale 1 ns/1 ps

module disp_b (
    input  logic [3:0] b_i [0:7],
    output logic [5:0] d_o [0:7],
    output logic [5:0] c_o
);

    logic [3:0] s1;
    logic [3:0] s3;

    always_comb begin
        s1 = '0;
        s3 = '0;
        for (int k = 0; k < 8; k++) begin
            s1 = s1 + b_i[k][1];
            s3 = s3 + b_i[k][3];
        end
    end

    assign c_o = s1 + (s3 << 2);

    genvar k;
    generate
        for (k = 0; k < 8; k++) begin : g_dig
            assign d_o[k][0] = b_i[k][0];
            assign d_o[k][1] = b_i[k][1] & ~b_i[k][0];
            assign d_o[k][2] = b_i[k][1];
            assign d_o[k][3] = b_i[k][1] ^ b_i[k][2];
            assign d_o[k][4] = (b_i[k][2] & b_i[k][1] & ~b_i[k][3]) |
                               (~b_i[k][2] & ~b_i[k][1] & b_i[k][3]);
            assign d_o[k][5] = b_i[k][3];
        end
    endgenerate

endmodule
```

## dp_8.sv (with csa3)

```systemverilog
// file: dp_8.sv
// ---------------------------------------------------------------------------
// dp_8: the dot product as 16 narrow Booth partial products with inverted sign
// bits + one merged constant row (17 rows), a balanced 3:2 CSA tree over columns
// 0..14 and a final plain 15-bit add.  Details in the file header above.
//   b_i[k] = {neg1, m1_1, m1_0, neg0, m0_1, m0_0}
//   c_i = popcount(b1) + 4*popcount(b3)          (6 bits, negation carry-in)
//   X = mux{0,a,2a} with the sign bit x[8] = (m0|m1)&a[7];
//   s0/s1 = x[8]^neg, rows use the INVERTED sign bit ~s0/~s1 so each row
//   over-contributes a constant (256/1024), and the single constant row
//   c_row = {1'b1,1'b0,1'b1,1'b1,5'b0,c_i} (cols 0..5, 11, 12, 14) subtracts
//   the 10240 total over-contribution.  c_row enters at stage 2 (u21 with
//   t1[3], t1[4]).
// ---------------------------------------------------------------------------

`timescale 1 ns/1 ps

module dp_8 (
    input  logic [ 7:0] a_i [0:7],
    input  logic [ 5:0] b_i [0:7],
    input  logic [ 5:0] c_i,
    output logic [14:0] y_o
);

    function automatic logic [8:0] x9(input logic [7:0] a,
                                      input logic m1,
                                      input logic m0);
        logic [8:0] x;
        begin
            x[0] = m0 & a[0];
            for (int j = 1; j < 8; j++) begin
                x[j] = (m0 & a[j]) | (m1 & a[j-1]);
            end
            x[8] = (m0 | m1) & a[7];
            x9 = x;
        end
    endfunction

    logic [8:0]  x0    [0:7];
    logic [8:0]  x1    [0:7];
    logic [7:0]  s0;
    logic [7:0]  s1;
    logic [14:0] r     [0:16];
    logic [14:0] c_row;
    logic [14:0] t0    [0:11];
    logic [14:0] t1    [0:7];
    logic [14:0] t2    [0:5];
    logic [14:0] t3    [0:3];
    logic [14:0] t4    [0:2];
    logic [14:0] t5    [0:1];
    logic [14:0] y;

    genvar k;
    generate
        for (k = 0; k < 8; k++) begin : g_pp
            assign x0[k] = x9(a_i[k], b_i[k][1], b_i[k][0]);
            assign x1[k] = x9(a_i[k], b_i[k][4], b_i[k][3]);
            assign s0[k] = x0[k][8] ^ b_i[k][2];
            assign s1[k] = x1[k][8] ^ b_i[k][5];
            assign r[2*k]   = {~s0[k], x0[k][7:0] ^ {8{b_i[k][2]}}};
            assign r[2*k+1] = {~s1[k], x1[k][7:0] ^ {8{b_i[k][5]}}, 2'b0};
        end
    endgenerate

    assign c_row = {1'b1, 1'b0, 1'b1, 1'b1, 5'b0, c_i};

    csa3 #(.W(15)) u00 (.a(r[0]),  .b(r[2]),  .c(r[4]),  .s(t0[0]), .co(t0[1]));
    csa3 #(.W(15)) u01 (.a(r[6]),  .b(r[8]),  .c(r[10]), .s(t0[2]), .co(t0[3]));
    csa3 #(.W(15)) u02 (.a(r[1]),  .b(r[3]),  .c(r[5]),  .s(t0[4]), .co(t0[5]));
    csa3 #(.W(15)) u03 (.a(r[7]),  .b(r[9]),  .c(r[11]), .s(t0[6]), .co(t0[7]));
    csa3 #(.W(15)) u04 (.a(r[12]), .b(r[13]), .c(r[14]), .s(t0[8]), .co(t0[9]));
    assign t0[10] = r[15];
    assign t0[11] = c_row;

    csa3 #(.W(15)) u10 (.a(t0[0]), .b(t0[2]), .c(t0[8]),  .s(t1[0]), .co(t1[1]));
    csa3 #(.W(15)) u11 (.a(t0[1]), .b(t0[3]), .c(t0[9]),  .s(t1[2]), .co(t1[3]));
    csa3 #(.W(15)) u12 (.a(t0[4]), .b(t0[6]), .c(t0[10]), .s(t1[4]), .co(t1[5]));
    assign t1[6] = t0[5];
    assign t1[7] = t0[7];

    csa3 #(.W(15)) u20 (.a(t1[0]), .b(t1[1]), .c(t1[2]),  .s(t2[0]), .co(t2[1]));
    csa3 #(.W(15)) u21 (.a(t1[3]), .b(t1[4]), .c(c_row),  .s(t2[2]), .co(t2[3]));
    csa3 #(.W(15)) u22 (.a(t1[5]), .b(t1[6]), .c(t1[7]),  .s(t2[4]), .co(t2[5]));

    csa3 #(.W(15)) u30 (.a(t2[0]), .b(t2[1]), .c(t2[2]),  .s(t3[0]), .co(t3[1]));
    csa3 #(.W(15)) u31 (.a(t2[3]), .b(t2[4]), .c(t2[5]),  .s(t3[2]), .co(t3[3]));

    csa3 #(.W(15)) u40 (.a(t3[0]), .b(t3[1]), .c(t3[2]),  .s(t4[0]), .co(t4[1]));
    assign t4[2] = t3[3];

    csa3 #(.W(15)) u50 (.a(t4[0]), .b(t4[1]), .c(t4[2]),  .s(t5[0]), .co(t5[1]));

    assign y   = t5[0] + t5[1];
    assign y_o = y;

endmodule

// csa3: one balanced carry-save stage.  For each column i: s[i] is the full-adder
// sum of a[i],b[i],c[i] and co[i] (i>0) is the majority carry out of column i-1
// (co[0] = 0).  The two output rows s and co represent the same total as the three
// input rows; the carry out of column W-1 is dropped (arithmetic mod 2^W).

module csa3 #(parameter W = 15) (
    input  logic [W-1:0] a,
    input  logic [W-1:0] b,
    input  logic [W-1:0] c,
    output logic [W-1:0] s,
    output logic [W-1:0] co
);

    genvar i;
    generate
        for (i = 0; i < W; i++) begin : g_fa
            assign s[i]  = a[i] ^ b[i] ^ c[i];
            assign co[i] = (i == 0) ? 1'b0
                         : (a[i-1] & b[i-1]) | (a[i-1] & c[i-1]) | (b[i-1] & c[i-1]);
        end
    endgenerate

endmodule
```
