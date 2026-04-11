<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

This is an 8-bit, 2-stage pipelined CPU with a custom ISA inspired by ARM.

### Pipeline stages

The CPU has two pipeline stages:

- **Fetch**: The PC is placed on `uo_out[6:0]`. External memory drives `ui_in` with the instruction at that address. On the next rising edge the instruction is latched into the instruction register (IR).
- **Execute**: The control unit decodes the IR, the register file is read, the ALU computes the result, and write-back happens on the same rising edge.

Because these two stages overlap, one instruction is being fetched while the previous one is executing. The effective throughput is one instruction per cycle when there are no stalls.

### Memory interface

The CPU uses a **Von Neumann** (unified) memory model — instructions and data share the same 7-bit address bus (`uo_out[6:0]`, 128 locations) and 8-bit data bus (`ui_in`). External memory must be word-addressable with a one-cycle read latency (combinational output, registered on the CPU's rising edge).

On a load (LDR) or store (STR), the CPU hardware stalls the pipeline for two cycles:

1. Cycle 1: `uo_out[6:0]` switches from PC to the data address (Rs register value). Memory output is not yet valid.
2. Cycle 2: `uo_out[6:0]` holds the data address. Memory output is valid. For LDR, the value on `ui_in` is written to Rd. For STR, `uio_out` holds the store value and `uo_out[7]` (WE#) is driven low.

After the two stall cycles, the pipeline resumes automatically. No software NOP padding is required.

### Registers

Four general-purpose 8-bit registers: R0, R1, R2, R3.

Two condition flags, updated only by CMP:
- **Z (zero)**: set when the result is zero
- **C (carry)**: set when there is no borrow (i.e. Rd >= Rs for subtraction)

### Instruction set

All instructions are 8 bits wide. There are three formats:

**R-type** `0_ooo_dd_ss` — register operations

| Mnemonic | Encoding (ooo) | Operation |
|---|---|---|
| ADD Rd, Rs | 000 | Rd = Rd + Rs |
| SUB Rd, Rs | 001 | Rd = Rd − Rs |
| AND Rd, Rs | 010 | Rd = Rd & Rs |
| OR  Rd, Rs | 011 | Rd = Rd \| Rs |
| MOV Rd, Rs | 100 | Rd = Rs |
| CMP Rd, Rs | 101 | Sets Z and C flags; no register write |
| LDR Rd, Rs | 110 | Rd = mem[Rs] |
| STR Rd, Rs | 111 | mem[Rs] = Rd |

**I-type** `10_dd_iiii` — load immediate

| Mnemonic | Operation |
|---|---|
| MOVI Rd, #imm | Rd = zero\_extend(imm[3:0]) |

The 4-bit immediate is zero-extended to 8 bits. Range: 0–15.

**B-type** `11_cc_oooo` — conditional branch

| Mnemonic | Condition (cc) | Taken when |
|---|---|---|
| BEQ offset | 00 | Z = 1 |
| BNE offset | 01 | Z = 0 |
| BCS offset | 10 | C = 1 (no borrow, Rd >= Rs) |
| B   offset | 11 | always |

The 4-bit signed offset is sign-extended. Branch target = (PC + 1) + 1 + offset, where PC+1 is the already-incremented fetch pointer. To branch to absolute address T from instruction at address N, use offset = T − N − 2. Range: −8 to +7 from the instruction after the branch.

When a branch is taken, the instruction in the fetch stage is flushed (one cycle bubble). No stall occurs for a not-taken branch.

### Pinout

| Pin | Direction | Description |
|---|---|---|
| `ui_in[7:0]` | Input | Data bus from external memory (instructions and load data) |
| `uo_out[6:0]` | Output | 7-bit address bus to external memory (PC during fetch, Rs during memory op) |
| `uo_out[7]` | Output | WE# — active-low write enable, asserted during STR memory cycle |
| `uio_out[7:0]` | Output | Store data bus (value of Rd during STR memory cycle) |

## How to test

### Simulation

Run the cocotb testbench:

```bash
cd test && make -B
```

This runs 15 tests covering all instructions, pipeline stalls, branch conditions, and a STR→LDR round-trip. A waveform is written to `test/tb.fst` and can be opened in GTKWave or Surfer.

### With real hardware

Connect an 8-bit SRAM (e.g. 23LC512 or IS61C256AH) to the Tiny Tapeout board:

```
CPU pin         SRAM pin
uo_out[6:0]   → address bus A[6:0]   (SRAM's upper address bits A[n:7] tied to GND)
uo_out[7]     → write enable WE#     (active low, connect directly)
ui_in[7:0]    ← data out             (SRAM output → CPU input)
uio_out[7:0]  → data in              (CPU output → SRAM input)
GND           → output enable OE#    (tie low)
GND           → chip enable CE#      (tie low)
```

Pre-load the SRAM with your program using a microcontroller or programmer before asserting `rst_n`. On reset release the CPU begins executing from address 0.

**Program layout guidelines:**
- Place executable code starting at address 0.
- Place data (load/store targets) at addresses your code never fetches as instructions. A common pattern is to place data after an unconditional branch that halts or loops the CPU.
- Branch offsets are 4-bit signed (−8 to +7 instructions from the branch). Keep branch targets within this range, or use a chain of branches for longer jumps.
- Constants larger than 15 must be loaded from memory with LDR rather than MOVI.

### Example program

The following program loads a value from memory, adds a constant, and stores the result:

```
addr 0: 10_01_1000  MOVI R1, #8    -- R1 = 8 (data address)
addr 1: 10_10_0011  MOVI R2, #3    -- R2 = 3 (addend)
addr 2: 0_110_00_01  LDR  R0, R1   -- R0 = mem[8]
addr 3: 0_000_00_10  ADD  R0, R2   -- R0 = R0 + 3
addr 4: 0_111_00_01  STR  R0, R1   -- mem[8] = result
addr 5: 11_11_1110  B    -2        -- halt (self-loop: offset=-2 → target=5)
addr 6: (unused)
addr 7: (unused)
addr 8: 0100_0010                  -- data value
```


## External hardware

A byte-addressable 8-bit SRAM with combinational read and one write enable signal. The SRAM must:
- Output the data at the addressed location combinationally (output valid within one clock half-period after address is presented on the falling edge)
- Accept a write when `uo_out[7]` (WE#) is driven low (active-low, connect directly to SRAM WE#)
- Support at least 128 addressable bytes