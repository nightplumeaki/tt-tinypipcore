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
  wire        mem_wr_en;
  wire [6:0]  addr_bus;   // 7-bit address from core

  nightplumeaki_top core (
    .clk       (clk),
    .rst_n     (rst_n),
    .data_in   (ui_in),      // instructions + load data from external memory
    .addr_out  (addr_bus),   // 7-bit address bus (128 locations)
    .store_data(uio_out),    // store data for STR on uio_out[7:0]
    .mem_wr_en (mem_wr_en)
  );

  // uo_out[6:0]: 7-bit address bus
  // uo_out[7]:   active-low WE# for external SRAM, low only during STR mem_cycle
  assign uo_out[6:0] = addr_bus;
  assign uo_out[7]   = ~mem_wr_en;

  // uio bidir pins are always driven as outputs (store data + always-valid).
  // uio_oe is pad direction control only — it does not appear as an external
  // pin, so WE# must be carried on uo_out[7] instead.
  assign uio_oe = 8'hFF;

  // List all unused inputs to prevent warnings
  wire _unused = &{ena, uio_in, 1'b0};

endmodule
