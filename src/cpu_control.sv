module cpu_control (
    input  logic [7:4] instr,        // top 4 bits of instruction
    input  logic       zero, carry_out,
    output logic       RegWrite, UpdateFlags, MemRead, MemWrite,
    output logic [1:0] WBSel,        // 0=ALU, 1=mem, 2=imm
    output logic [2:0] ALUop,
    output logic       BrTaken
);

    // Format detection
    wire is_rtype = ~instr[7];
    wire is_itype =  instr[7] & ~instr[6];
    wire is_btype =  instr[7] &  instr[6];

    always_comb begin
        // Defaults
        RegWrite    = 0;
        UpdateFlags = 0;
        MemRead     = 0;
        MemWrite    = 0;
        WBSel       = 2'b00;
        ALUop       = 3'b000;
        BrTaken     = 0;

        if (is_rtype) begin
            // instr[6:4] = ooo (sub-opcode)
            case (instr[6:4])
                3'b000: begin ALUop = 3'b010; RegWrite = 1; end             // ADD
                3'b001: begin ALUop = 3'b011; RegWrite = 1; end             // SUB
                3'b010: begin ALUop = 3'b100; RegWrite = 1; end             // AND
                3'b011: begin ALUop = 3'b101; RegWrite = 1; end             // OR
                3'b100: begin ALUop = 3'b000; RegWrite = 1; end             // MOV (pass B)
                3'b101: begin ALUop = 3'b011; UpdateFlags = 1; end          // CMP (SUB, no write)
                3'b110: begin MemRead = 1; RegWrite = 1; WBSel = 2'b01; end // LDR
                3'b111: begin MemWrite = 1; end                             // STR
            endcase

        end else if (is_itype) begin
            // MOVI: write immediate to Rd
            RegWrite = 1;
            WBSel    = 2'b10;  // select immediate

        end else begin
            // B-type: instr[5:4] = cc (condition)
            case (instr[5:4])
                2'b00: BrTaken = zero;              // BEQ
                2'b01: BrTaken = ~zero;             // BNE
                2'b10: BrTaken = carry_out;         // BCS
                2'b11: BrTaken = 1;                 // B
            endcase
        end
    end

endmodule // cpu_control