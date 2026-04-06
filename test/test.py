# SPDX-FileCopyrightText: © 2026 Yuxuan Liu
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

# === ISA encoding helpers ===
# These turn assembly-like calls into 8-bit machine code.
# R-type: 0_ooo_dd_ss
def ADD(rd, rs): return (0b0_000 << 4) | (rd << 2) | rs
def SUB(rd, rs): return (0b0_001 << 4) | (rd << 2) | rs
def AND(rd, rs): return (0b0_010 << 4) | (rd << 2) | rs
def OR(rd, rs):  return (0b0_011 << 4) | (rd << 2) | rs
def MOV(rd, rs): return (0b0_100 << 4) | (rd << 2) | rs
def CMP(rd, rs): return (0b0_101 << 4) | (rd << 2) | rs
def LDR(rd, rs): return (0b0_110 << 4) | (rd << 2) | rs
def STR(rd, rs): return (0b0_111 << 4) | (rd << 2) | rs

# I-type: 10_dd_iiii
def MOVI(rd, imm): return (0b10 << 6) | (rd << 4) | (imm & 0xF)

# B-type: 11_cc_oooo  (offset is 4-bit signed)
def BEQ(off): return (0b11_00 << 4) | (off & 0xF)
def BNE(off): return (0b11_01 << 4) | (off & 0xF)
def BCS(off): return (0b11_10 << 4) | (off & 0xF)
def B(off):   return (0b11_11 << 4) | (off & 0xF)

def NOP(): return MOV(0, 0)  # MOV R0, R0

# Register names for readability
R0, R1, R2, R3 = 0, 1, 2, 3


async def reset(dut):
    """Reset the CPU and wait for it to be ready."""
    dut.rst_n.value = 0
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def run_cpu(dut, memory, num_cycles):
    """
    Run the CPU for num_cycles clock cycles.
    Acts as the external memory controller:
      - Each cycle: read address from uo_out, feed memory[addr] to ui_in
      - If uio_oe == 0xFF (STR): write uio_out to memory[addr]
    """
    for _ in range(num_cycles):
        await RisingEdge(dut.clk)

        # Read address from CPU
        addr = dut.uo_out.value.integer

        # Check if CPU is writing (STR)
        if dut.uio_oe.value == 0xFF:
            data = dut.uio_out.value.integer
            memory[addr] = data
            dut._log.info(f"  STR: memory[{addr}] = {data}")

        # Feed data back to CPU (instruction or load data)
        dut.ui_in.value = memory[addr]


@cocotb.test()
async def test_movi_and_add(dut):
    """Test: MOVI R0, #3 ; MOVI R1, #5 ; ADD R0, R1 → R0 should be 8"""
    dut._log.info("=== Test MOVI and ADD ===")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    memory = [NOP()] * 256
    # Program: R0 = 3 + 5 = 8, then store to memory[15]
    memory[0] = MOVI(R0, 3)       # R0 = 3
    memory[1] = MOVI(R1, 5)       # R1 = 5
    memory[2] = ADD(R0, R1)       # R0 = R0 + R1 = 8
    memory[3] = MOVI(R2, 15)      # R2 = 15 (store address)
    memory[4] = STR(R0, R2)       # memory[15] = R0

    await reset(dut)

    # Feed initial instruction
    dut.ui_in.value = memory[0]

    # Run enough cycles (each instr = 1 cycle, STR/LDR = 2 cycles, + pipeline fill)
    await run_cpu(dut, memory, 15)

    # Check: memory[15] should be 8
    assert memory[15] == 8, f"Expected memory[15]=8, got {memory[15]}"
    dut._log.info("PASS: R0 = 3 + 5 = 8, stored to memory[15]")


@cocotb.test()
async def test_sub(dut):
    """Test: MOVI R0, #10 ; MOVI R1, #4 ; SUB R0, R1 → R0 should be 6"""
    dut._log.info("=== Test SUB ===")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    memory = [NOP()] * 256
    memory[0] = MOVI(R0, 10)      # R0 = 10
    memory[1] = MOVI(R1, 4)       # R1 = 4
    memory[2] = SUB(R0, R1)       # R0 = 10 - 4 = 6
    memory[3] = MOVI(R2, 15)      # R2 = 15
    memory[4] = STR(R0, R2)       # memory[15] = R0

    await reset(dut)
    dut.ui_in.value = memory[0]
    await run_cpu(dut, memory, 15)

    assert memory[15] == 6, f"Expected memory[15]=6, got {memory[15]}"
    dut._log.info("PASS: R0 = 10 - 4 = 6")


@cocotb.test()
async def test_and_or(dut):
    """Test: AND and OR operations"""
    dut._log.info("=== Test AND and OR ===")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    memory = [NOP()] * 256
    memory[0] = MOVI(R0, 0b1111)  # R0 = 15
    memory[1] = MOVI(R1, 0b0110)  # R1 = 6
    memory[2] = AND(R0, R1)       # R0 = 15 & 6 = 6 (0b0110)
    memory[3] = MOVI(R2, 14)      # R2 = 14
    memory[4] = STR(R0, R2)       # memory[14] = R0 (AND result)
    # OR test
    memory[5] = MOVI(R0, 0b1010)  # R0 = 10
    # STR takes extra cycle, so addr 5 is fetched after stall
    memory[6] = MOVI(R1, 0b0101)  # R1 = 5
    memory[7] = OR(R0, R1)        # R0 = 10 | 5 = 15
    memory[8] = MOVI(R2, 13)      # R2 = 13
    memory[9] = STR(R0, R2)       # memory[13] = R0 (OR result)

    await reset(dut)
    dut.ui_in.value = memory[0]
    await run_cpu(dut, memory, 25)

    assert memory[14] == 6, f"Expected memory[14]=6 (AND), got {memory[14]}"
    assert memory[13] == 15, f"Expected memory[13]=15 (OR), got {memory[13]}"
    dut._log.info("PASS: AND=6, OR=15")


@cocotb.test()
async def test_ldr_str(dut):
    """Test: Load from memory, add, store back"""
    dut._log.info("=== Test LDR and STR ===")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    memory = [NOP()] * 256
    # Pre-load data in memory
    memory[100] = 42
    memory[101] = 18

    # Program: load two values, add them, store result
    memory[0] = MOVI(R3, 0)       # R3 = 0 (will build address 100)
    # Can't MOVI 100 (>15), so we build it: load from a known location
    # Simpler: use values at low addresses
    # Let's put data at addresses 10 and 11 instead
    memory[10] = 42
    memory[11] = 18

    memory[0] = MOVI(R2, 10)      # R2 = 10 (address of first value)
    memory[1] = LDR(R0, R2)       # R0 = memory[10] = 42
    memory[2] = MOVI(R2, 11)      # R2 = 11
    memory[3] = LDR(R1, R2)       # R1 = memory[11] = 18
    memory[4] = ADD(R0, R1)       # R0 = 42 + 18 = 60
    memory[5] = MOVI(R2, 12)      # R2 = 12 (store address)
    memory[6] = STR(R0, R2)       # memory[12] = 60

    await reset(dut)
    dut.ui_in.value = memory[0]
    await run_cpu(dut, memory, 25)

    assert memory[12] == 60, f"Expected memory[12]=60, got {memory[12]}"
    dut._log.info("PASS: memory[10] + memory[11] = 42 + 18 = 60")


@cocotb.test()
async def test_branch(dut):
    """Test: CMP + BEQ (branch taken and not taken)"""
    dut._log.info("=== Test Branch ===")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    memory = [NOP()] * 256
    # Program: if R0 == R1, store 1 to memory[15], else store 0
    memory[0] = MOVI(R0, 7)       # R0 = 7
    memory[1] = MOVI(R1, 7)       # R1 = 7
    memory[2] = CMP(R0, R1)       # compare: Z=1 (equal)
    memory[3] = BEQ(2)            # if Z=1, jump to PC+1+2 = addr 6
    memory[4] = MOVI(R3, 0)       # R3 = 0 (not taken path)
    memory[5] = B(2)              # skip to addr 8
    memory[6] = MOVI(R3, 1)       # R3 = 1 (taken path)
    memory[7] = NOP()
    memory[8] = MOVI(R2, 15)      # R2 = 15
    memory[9] = STR(R3, R2)       # memory[15] = R3

    await reset(dut)
    dut.ui_in.value = memory[0]
    await run_cpu(dut, memory, 25)

    assert memory[15] == 1, f"Expected memory[15]=1 (branch taken), got {memory[15]}"
    dut._log.info("PASS: BEQ taken, R3=1 stored")


@cocotb.test()
async def test_branch_not_taken(dut):
    """Test: CMP + BEQ where branch is NOT taken"""
    dut._log.info("=== Test Branch Not Taken ===")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    memory = [NOP()] * 256
    memory[0] = MOVI(R0, 3)       # R0 = 3
    memory[1] = MOVI(R1, 5)       # R1 = 5
    memory[2] = CMP(R0, R1)       # compare: Z=0 (not equal)
    memory[3] = BEQ(2)            # if Z=1, jump → NOT taken
    memory[4] = MOVI(R3, 0)       # R3 = 0 (this should execute)
    memory[5] = B(2)              # skip to addr 8
    memory[6] = MOVI(R3, 1)       # R3 = 1 (should NOT execute)
    memory[7] = NOP()
    memory[8] = MOVI(R2, 15)      # R2 = 15
    memory[9] = STR(R3, R2)       # memory[15] = R3

    await reset(dut)
    dut.ui_in.value = memory[0]
    await run_cpu(dut, memory, 25)

    assert memory[15] == 0, f"Expected memory[15]=0 (branch not taken), got {memory[15]}"
    dut._log.info("PASS: BEQ not taken, R3=0 stored")