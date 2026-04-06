/*
 * Copyright (c) 2026 Yuxuan Liu
 * SPDX-License-Identifier: Apache-2.0
 */

// cntrl		Operation:
// 000:			result = B
// 010:			result = A + B
// 011:			result = A - B
// 100:			result = A & B
// 101:			result = A | B
// 110:			result = A ^ B

module alu (A, B, cntrl, result, negative, zero, overflow, carry_out);

	input  logic [7:0] A, B;
	input  logic [2:0] cntrl;
	output logic [7:0] result;
	output logic       negative, zero, overflow, carry_out;

	logic [7:0] b_port;
	logic [8:0] sum;

	// Negate B for subtraction (cntrl[0] == 1), cin also set to 1 via cntrl[0]
	assign b_port = cntrl[0] ? ~B : B;

	// Full-width add with carry-in for two's complement subtraction
	assign sum = {1'b0, A} + {1'b0, b_port} + {8'b0, cntrl[0]};

	always_comb begin
		case (cntrl)
			3'b000: result = B;
			3'b010: result = sum[7:0];
			3'b011: result = sum[7:0];
			3'b100: result = A & B;
			3'b101: result = A | B;
			3'b110: result = A ^ B;
			default: result = 8'b0;
		endcase
	end

	assign carry_out = sum[8];
	assign overflow  = (A[7] == b_port[7]) && (result[7] != A[7]);
	assign negative  = result[7];
	assign zero      = (result == 8'b0);

endmodule // alu
