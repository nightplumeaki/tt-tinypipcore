/*
 * Copyright (c) 2026 Yuxuan Liu
 * SPDX-License-Identifier: Apache-2.0
 */

module nightplumeaki_top (
    input  logic       clk,
    input  logic       rst_n,
    input  logic [7:0] data_in,      // ui_in
    output logic [7:0] addr_out,     // uo_out
    output logic [7:0] store_data,   // uio_out
    output logic       mem_wr_en     // for uio_oe
);

    // === Internal wires ===
    logic [7:0] instr;
    logic       IR_valid;
    logic [7:0] pc;
    logic [7:0] A, B, ALUResult;
    logic       negative, zero, overflow, carry_out;
    logic       flag_z, flag_c;
    logic       RegWrite, UpdateFlags, MemRead, MemWrite, BrTaken;
    logic [1:0] WBSel;
    logic [2:0] ALUop;
    logic       mem_cycle;

    // === Derived signals ===
    wire mem_active = (MemRead | MemWrite) & IR_valid;
    wire stall     = mem_active & ~mem_cycle;
    wire flush     = BrTaken & IR_valid;

    // Rd address mux: I-type uses instr[5:4], R-type uses instr[3:2]
    wire is_itype  = instr[7] & ~instr[6];
    wire [1:0] rd_addr = is_itype ? instr[5:4] : instr[3:2];

    // Write-back mux
    logic [7:0] wb_data;
    always_comb begin
        case (WBSel)
            2'b00:   wb_data = ALUResult;
            2'b01:   wb_data = data_in;
            2'b10:   wb_data = {4'b0, instr[3:0]};
            default: wb_data = 8'b0;
        endcase
    end

    // Branch target: PC+1 + sign-extended offset
    wire [7:0] pc_plus1      = pc + 8'd1;
    wire [7:0] branch_target = pc_plus1 + {{4{instr[3]}}, instr[3:0]};

    // === Pipeline register (IR + valid) ===
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            instr    <= 8'b0;
            IR_valid <= 1'b0;
        end else if (flush) begin
            IR_valid <= 1'b0;
        end else if (!stall) begin
            instr    <= data_in;
            IR_valid <= 1'b1;
        end
    end

    // === Program counter ===
    always_ff @(posedge clk) begin
        if (!rst_n)
            pc <= 8'b0;
        else if (flush)
            pc <= branch_target;
        else if (!stall)
            pc <= pc_plus1;
    end

    // === Memory cycle tracker ===
    always_ff @(posedge clk) begin
        if (!rst_n)
            mem_cycle <= 1'b0;
        else
            mem_cycle <= mem_active & ~mem_cycle;
    end

    // === Flag register ===
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            flag_z <= 1'b0;
            flag_c <= 1'b0;
        end else if (UpdateFlags & IR_valid) begin
            flag_z <= zero;
            flag_c <= carry_out;
        end
    end

    // === Control unit ===
    cpu_control control (
        .instr    (instr[7:4]),
        .zero     (flag_z),
        .carry_out(flag_c),
        .RegWrite (RegWrite),
        .UpdateFlags(UpdateFlags),
        .MemRead  (MemRead),
        .MemWrite (MemWrite),
        .WBSel    (WBSel),
        .ALUop    (ALUop),
        .BrTaken  (BrTaken)
    );

    // === Register file ===
    regfile registers (
        .clk     (clk),
        .reset   (rst_n),
        .Ad      (rd_addr),
        .As      (instr[1:0]),
        .Aw      (rd_addr),
        .Dw      (wb_data),
        .RegWrite(RegWrite & IR_valid),
        .Dd      (A),
        .Ds      (B)
    );

    // === ALU ===
    alu arithmetic (
        .A        (A),
        .B        (B),
        .cntrl    (ALUop),
        .result   (ALUResult),
        .negative (negative),
        .zero     (zero),
        .overflow (overflow),
        .carry_out(carry_out)
    );

    // === Output wiring ===
    assign addr_out   = mem_active ? B : pc;        // address mux
    assign store_data = A;                           // Rd value for STR
    assign mem_wr_en  = MemWrite & IR_valid;         // write enable

endmodule // nightplumeaki_top