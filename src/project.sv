/*
 * Copyright (c) 2026 Yuxuan Liu
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_nightplumeaki_tinypipcore (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  // CPU core
  wire mem_wr_en;

  nightplumeaki_top core (
    .clk       (clk),
    .rst_n     (rst_n),
    .data_in   (ui_in),        // instructions + load data from external memory
    .addr_out  (uo_out),       // address bus (PC or data address)
    .store_data(uio_out),      // Rd value for STR
    .mem_wr_en (mem_wr_en)
  );

  // Drive all 8 bidir pins as outputs during STR, inputs otherwise
  assign uio_oe = mem_wr_en ? 8'hFF : 8'h00;

  // List all unused inputs to prevent warnings
  wire _unused = &{ena, uio_in, 1'b0};

endmodule
