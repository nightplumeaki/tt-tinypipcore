module regfile (
    input  logic       clk,
    input  logic       reset,
    input  logic [1:0] Ad,    // Rd address (read port 1)
    input  logic [1:0] As,    // Rs address (read port 2)
    input  logic [1:0] Aw,    // Write address
    input  logic [7:0] Dw,    // Write data
    input  logic       RegWrite,  // Write enable
    output logic [7:0] Dd,    // Rd data out
    output logic [7:0] Ds     // Rs data out
);

    // Storage: 4 registers, 8 bits each
    logic [7:0] regs [0:3];

    // Reads
    assign Dd = regs[Ad];
    assign Ds = regs[As];

    // Write
    always_ff @(posedge clk) begin
        if (!reset) begin
            regs[0] <= 8'b0;
            regs[1] <= 8'b0;
            regs[2] <= 8'b0;
            regs[3] <= 8'b0;
        end else if (RegWrite) begin
            regs[Aw] <= Dw;
        end
    end

endmodule // regfile