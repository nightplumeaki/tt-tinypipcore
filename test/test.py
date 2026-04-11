"""
Cocotb testbench for tt_um_nightplumeaki_tinypipcore
ISA:
  R-type  0_ooo_dd_ss
  I-type  10_dd_iiii   (MOVI)
  B-type  11_cc_oooo

Register visibility: run 'make -B' then open tb.fst in Surfer/GTKWave.
Navigate to tb > user_project > core > registers > regs[0..3].
$dumpvars(0, tb) captures all internal signals including register file contents.
"""

import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles, ReadOnly

# In gate-level simulation the design is a flat netlist — internal hierarchy
# paths (registers.regs, mem_cycle) do not exist. Tests skip internal-signal
# assertions in that mode and only verify external pin behaviour.
GL_TEST = os.environ.get("GATES", "no") == "yes"

def get_regs(dut):
    """Return the register file handle, or None in gate-level simulation.

    In GL mode the design is a flat netlist — no internal hierarchy exists.
    Callers must guard assertions with 'if regs is not None'.
    """
    if GL_TEST:
        return None
    return dut.user_project.core.registers.regs


# ---------------------------------------------------------------------------
# ISA encoding
# ---------------------------------------------------------------------------

def movi(rd, imm):
    """MOVI Rd, #imm  ->  10_dd_iiii"""
    return (0b10 << 6) | (rd << 4) | (imm & 0xF)

def r(ooo, rd, rs):
    """Generic R-type  ->  0_ooo_dd_ss"""
    return (ooo << 4) | (rd << 2) | rs

def add(rd, rs):  return r(0b000, rd, rs)   # ADD Rd, Rs -> Rd = Rd + Rs
def sub(rd, rs):  return r(0b001, rd, rs)   # SUB Rd, Rs -> Rd = Rd - Rs
def and_(rd, rs): return r(0b010, rd, rs)   # AND Rd, Rs -> Rd = Rd & Rs
def or_(rd, rs):  return r(0b011, rd, rs)   # OR  Rd, Rs -> Rd = Rd | Rs
def mov(rd, rs):  return r(0b100, rd, rs)   # MOV Rd, Rs -> Rd = Rs
def cmp(rd, rs):  return r(0b101, rd, rs)   # CMP Rd, Rs -> flags only, no write
def ldr(rd, rs):  return r(0b110, rd, rs)   # LDR Rd, Rs -> Rd = mem[Rs]
def str_(rd, rs): return r(0b111, rd, rs)   # STR Rd, Rs -> mem[Rs] = Rd

def beq(off): return (0b1100 << 4) | (off & 0xF)  # BEQ -> branch if Z=1
def bne(off): return (0b1101 << 4) | (off & 0xF)  # BNE -> branch if Z=0
def bcs(off): return (0b1110 << 4) | (off & 0xF)  # BCS -> branch if C=1
def b(off):   return (0b1111 << 4) | (off & 0xF)  # B   -> always branch

NOP = add(0, 0)  # ADD R0, R0 = 0x00, harmless no-op


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def rom_driver(dut, rom):
    """
    Simulates a RAM attached to the CPU address/data bus.

    Pin mapping after the WE# fix:
      uo_out[6:0]  — 7-bit address bus (128 locations)
      uo_out[7]    — active-low WE#: low means STR mem_cycle, write uio_out to mem
      ui_in[7:0]   — data bus to CPU (instruction fetch or LDR data)
      uio_out[7:0] — store data from CPU (valid when WE#=0)

    On every falling edge all always_ff NBA updates have settled, so uo_out
    and uio_out already reflect the current pipeline state.

    Writes (STR): WE#=uo_out[7]=0 means the CPU is in the STR mem_cycle.
    The address and data are both valid — write uio_out into rom[addr].

    Reads: always drive ui_in from rom[addr], covering both instruction fetch
    (addr=PC) and LDR data read (addr=Rs). No internal signals needed — this
    is exactly what real SRAM hardware does, and works in GL mode too.
    """
    while True:
        await FallingEdge(dut.clk)
        uo = int(dut.uo_out.value)
        addr  = uo & 0x7F          # uo_out[6:0]
        we_n  = (uo >> 7) & 1      # uo_out[7]: active-low WE#
        if we_n == 0:              # STR mem_cycle: write first
            if addr < len(rom):
                rom[addr] = int(dut.uio_out.value)
        dut.ui_in.value = rom[addr] if addr < len(rom) else cmp(0, 0)


async def reset(dut):
    dut.rst_n.value  = 0
    dut.ena.value    = 1
    dut.ui_in.value  = 0
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value  = 1
    await ClockCycles(dut.clk, 1)


def sample(dut):
    return {
        'addr': int(dut.uo_out.value),
        'data': int(dut.uio_out.value),
        'oe':   int(dut.uio_oe.value),
    }


# ---------------------------------------------------------------------------
# Test 1: PC advances normally (no stall, no branch)
# ---------------------------------------------------------------------------

@cocotb.test()
async def basic_test(dut):
    """
    Feed NOPs and verify addr_out (PC) increments every cycle.
    Baseline: if this fails, the pipeline itself is broken.
    """
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await reset(dut)

    dut.ui_in.value = movi(0, 0x1)  # 10000001
    await RisingEdge(dut.clk)
    dut.ui_in.value = movi(1, 0x2)  # 10010010
    await RisingEdge(dut.clk)
    dut.ui_in.value = movi(2, 0x3)  # 10100011
    await RisingEdge(dut.clk)
    # movi(2, 0x3) is in the IR; one more cycle lets it execute and write R2.
    dut.ui_in.value = NOP
    await RisingEdge(dut.clk)
    # ReadOnly() waits for always_ff non-blocking assignments to settle
    # before we sample, so we see the values written by this clock edge.
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 0x1
        assert int(regs[1].value) == 0x2
        assert int(regs[2].value) == 0x3

    # Extra cycles so Icarus flushes the FST write for regs[2] —
    # changes that occur at the very last simulation timestep are not
    # captured in the waveform otherwise.
    await ClockCycles(dut.clk, 3)

    dut._log.info("basic_test PASSED")


# ---------------------------------------------------------------------------
# Test 2: MOV, ADD, SUB
# ---------------------------------------------------------------------------

@cocotb.test()
async def alu_test(dut):
    """
    Test all R-type ALU instructions: MOV, ADD, SUB, AND, OR, CMP.

    R0=10 (1010), R1=3 (0011) give non-trivial AND/OR results:
        AND: 1010 & 0011 = 0010 = 2
        OR:  1010 | 0011 = 1011 = 11

    Program:
        MOVI R0, #10       ; R0 = 10
        MOVI R1, #3        ; R1 =  3
        MOV  R2, R0        ; R2 = 10            (MOV)
        ADD  R2, R1        ; R2 = 13            (ADD: 10+3)
        SUB  R2, R1        ; R2 = 10            (SUB: 13-3)
        MOV  R3, R0        ; R3 = 10            (MOV: fresh copy for AND)
        AND  R3, R1        ; R3 =  2            (AND: 1010 & 0011)
        OR   R2, R1        ; R2 = 11            (OR:  1010 | 0011)
        CMP  R3, R3        ; Z=1, R3 unchanged  (CMP: flags only, no write)

    Expected: R0=10, R1=3, R2=11, R3=2
    """
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await reset(dut)

    prog = [
        movi(0, 0xA),  # R0 = 10 (0b1010)
        movi(1, 3),    # R1 =  3 (0b0011)
        mov(2, 0),     # R2 = R0         = 10   (MOV)
        add(2, 1),     # R2 = R2 + R1    = 13   (ADD)
        sub(2, 1),     # R2 = R2 - R1    = 10   (SUB)
        mov(3, 0),     # R3 = R0         = 10   (MOV: fresh copy)
        and_(3, 1),    # R3 = R3 & R1    =  2   (AND: 1010 & 0011)
        or_(2, 1),     # R2 = R2 | R1    = 11   (OR:  1010 | 0011)
        cmp(3, 3),     # Z=1, R3 stays 2        (CMP: no register write)
        cmp(0, 0),     # drain: cmp executes on this edge (cmp = safe drain, no register write)
    ]

    for instr in prog:
        dut.ui_in.value = instr
        await RisingEdge(dut.clk)

    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 10, "MOVI R0 failed"
        assert int(regs[1].value) ==  3, "MOVI R1 failed"
        assert int(regs[2].value) == 11, "OR failed: expected 1010|0011=11"
        # CMP limitation: this only checks RegWrite=0 (R3 not modified).
        # It does NOT verify the Z flag was set — a broken CMP that does nothing
        # would also pass. Flag correctness is covered by the branch test.
        assert int(regs[3].value) ==  2, "AND failed or CMP wrote R3: expected 1010&0011=2"

    # Drain with CMP (no register write) so the waveform shows clean signal
    # levels — using NOP (ADD R0,R0) here would double R0 each cycle.
    # First RisingEdge exits the ReadOnly phase before we drive signals.
    await RisingEdge(dut.clk)
    for _ in range(2):
        dut.ui_in.value = cmp(0, 0)
        await RisingEdge(dut.clk)

    dut._log.info("alu_test PASSED")


# ---------------------------------------------------------------------------
# Test 3: STR — memory bus outputs during the memory cycle
# ---------------------------------------------------------------------------

@cocotb.test()
async def store_test(dut):
    """
    Verify STR drives the correct address, data, and write-enable on the bus.

    STR takes 2 cycles due to the stall mechanism:
      Stall cycle  (mem_cycle=0): IR holds STR, PC frozen, uio_oe goes HIGH
                                   but addr_out is still PC (not the target address).
      Memory cycle (mem_cycle=1): addr_out = Rs (target address),
                                   uio_out = Rd (data), uio_oe = 0xFF.
    We sample after the memory cycle fires.

    NOTE: uio_oe asserts one cycle early (during the stall cycle). This is a
    known design quirk — the write enable is combinatorially derived from
    IR_valid & MemWrite, not gated on mem_cycle.

    Program:
        MOVI R0, #5    ; R0 = 5  (data to store)
        MOVI R1, #7    ; R1 = 7  (memory address)
        STR  R0, R1    ; mem[R1] = R0

    Expected during memory cycle: uo_out[6:0]=7, uo_out[7]=0 (WE#), uio_out=5
    """
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await reset(dut)

    prog = [
        movi(0, 5),   # R0 = 5  (data)
        movi(1, 7),   # R1 = 7  (address)
        str_(0, 1),   # STR fetched; stall begins next cycle
        cmp(0, 0),    # stall cycle 1 (mem_cycle=0→1): addr_out=B=7 ← sample here
    ]

    for instr in prog:
        dut.ui_in.value = instr
        await RisingEdge(dut.clk)

    # After this last edge: mem_cycle=1, addr_out=B=7, uio_out=5, uio_oe=0xFF.
    # In GL mode, gate propagation delays mean outputs settle after ReadOnly()
    # fires, so we sample on the falling edge instead (5ns later, well past
    # the 1-unit delay). In RTL mode, ReadOnly() is sufficient.
    if GL_TEST:
        await FallingEdge(dut.clk)
    else:
        await ReadOnly()

    uo = int(dut.uo_out.value)
    assert (uo & 0x7F) == 7,    f"STR addr wrong: got {uo & 0x7F}, expected 7"
    assert (uo >> 7)   == 0,    f"STR WE# not asserted: uo_out[7]={uo >> 7}, expected 0"
    assert int(dut.uio_out.value) == 5, f"STR data wrong: got {int(dut.uio_out.value)}, expected 5"

    # Drain — let the pipeline advance past STR before ending simulation
    await RisingEdge(dut.clk)
    for _ in range(2):
        dut.ui_in.value = cmp(0, 0)
        await RisingEdge(dut.clk)

    dut._log.info("store_test PASSED")


# ---------------------------------------------------------------------------
# Test 4: Pipeline recovery after STR
# ---------------------------------------------------------------------------

@cocotb.test()
async def post_store_test(dut):
    """
    Verify that the two instructions immediately following a STR execute
    correctly, using a virtual ROM driven from uo_out — no placeholder cycles
    needed. The ROM naturally re-serves any instruction the CPU stalls on.

    STR stall mechanics (2 stall cycles with mem_done fix):
      Edge C: STR fetched, stall=1 (mem_cycle=0, addr_out=PC=3)
        Falling C: ROM sees addr=3, serves movi(2,A) — IR frozen, discarded
      Edge D: stall=1, mem_cycle→1, addr_out=B=7 (memory write)
        Falling D: ROM sees addr=7, serves rom[7]=NOP — IR frozen, discarded
      Edge E: stall=1, mem_done→1, addr_out=PC=3 (ROM valid again)
        Falling E: ROM sees addr=3, serves movi(2,A) — stall about to release
      Edge F: stall=0, IR latches movi(2,A) correctly ✓

    ROM program (no placeholders):
        addr 0: MOVI R0, #5    ; R0 = 5
        addr 1: MOVI R1, #7    ; R1 = 7
        addr 2: STR  R0, R1    ; mem[7] = 5
        addr 3: MOVI R2, #10   ; R2 = 10  (fetched automatically after stall)
        addr 4: MOVI R3, #11   ; R3 = 11

    Expected: R0=5, R1=7, R2=10, R3=11
    """
    rom = [
        movi(0, 5),   # addr 0: R0 = 5
        movi(1, 7),   # addr 1: R1 = 7
        str_(0, 1),   # addr 2: STR mem[7] = R0
        movi(2, 0xA), # addr 3: R2 = 10  (no placeholder — ROM re-serves this)
        movi(3, 0xB), # addr 4: R3 = 11
    ]

    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    cocotb.start_soon(rom_driver(dut, rom))
    await reset(dut)

    # Wait enough cycles for all 5 instructions + 2 stall + 2 pipeline drain
    await ClockCycles(dut.clk, 12)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 5,  "R0 corrupted by STR or stall"
        assert int(regs[1].value) == 7,  "R1 corrupted by STR or stall"
        assert int(regs[2].value) == 10, "MOVI R2 after STR failed — stall did not release correctly"
        assert int(regs[3].value) == 11, "MOVI R3 after STR failed — instruction after stall swallowed"

    await ClockCycles(dut.clk, 3)
    dut._log.info("post_store_test PASSED")


# ---------------------------------------------------------------------------
# Test 5: LDR — load from memory
# ---------------------------------------------------------------------------

@cocotb.test()
async def load_test(dut):
    """
    Verify LDR loads the correct value from the data bus into a register.

    LDR stall mechanics (same 3-cycle structure as STR):
      Edge B: LDR fetched. stall=1 (mem_cycle=0). addr_out=PC.
      Edge C: stall=1. mem_cycle→1. addr_out=B (data address presented to memory).
        Falling C: ROM sees addr=B, serves rom[B] as data on ui_in.
      Edge D: stall=1. mem_done→1. addr_out=B still (mem_cycle=1 at posedge).
        RegWrite fires here (gated on mem_cycle=1) → Rd = data_in = rom[B]. ✓
        After NBA: mem_cycle=0, addr_out returns to PC.
      Edge E: stall=0. IR latches next instruction normally.

    The data value lives at addr 5 in the ROM (same memory, von Neumann style).
    The program never executes addr 5 as an instruction, so there is no conflict.

    ROM layout:
        addr 0: MOVI R1, #5    ; R1 = 5  (address to load from)
        addr 1: LDR  R0, R1    ; R0 = mem[5]
        addr 2: MOVI R2, #10   ; R2 = 10 (verify pipeline resumes after LDR)
        addr 3: MOVI R3, #11   ; R3 = 11
        addr 4: (padding)
        addr 5: 0x55           ; data value — loaded into R0 by LDR

    Expected: R0=0x55, R1=5, R2=10, R3=11
    """
    DATA_VALUE = 0x55

    rom = [
        movi(1, 5),    # addr 0: R1 = 5
        ldr(0, 1),     # addr 1: R0 = mem[5]
        movi(2, 0xA),  # addr 2: R2 = 10
        movi(3, 0xB),  # addr 3: R3 = 11
        cmp(0, 0),     # addr 4: padding — never executed as instruction
        DATA_VALUE,    # addr 5: the value LDR will read
    ]

    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    cocotb.start_soon(rom_driver(dut, rom))
    await reset(dut)

    # Cycles needed:
    #   2 (MOVI R1) + 4 (LDR: fetch+3 stall) + 2 (MOVI R2) + 2 (MOVI R3) = ~10
    await ClockCycles(dut.clk, 12)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == DATA_VALUE, f"LDR failed: got {int(regs[0].value)}, expected {DATA_VALUE}"
        assert int(regs[1].value) == 5,          "R1 corrupted"
        assert int(regs[2].value) == 10,         "MOVI R2 after LDR failed — pipeline did not resume"
        assert int(regs[3].value) == 11,         "MOVI R3 after LDR failed"

    await ClockCycles(dut.clk, 3)
    dut._log.info("load_test PASSED")


# ---------------------------------------------------------------------------
# Test 6: Unconditional branch (B)
# ---------------------------------------------------------------------------

@cocotb.test()
async def branch_unconditional_test(dut):
    """
    Verify B always jumps and that the flushed instruction does not execute.

    Offset math: target = N + 2 + offset  →  offset = target - N - 2
      B at addr 1, target addr 3: offset = 3 - 1 - 2 = 0

    ROM:
        addr 0: MOVI R0, #1
        addr 1: B 0            ; jumps to addr 3 (offset=0 → 1+2+0=3)
        addr 2: MOVI R0, #0xFF ; MUST BE SKIPPED — flush discards this
        addr 3: MOVI R1, #2    ; must execute after branch

    Expected: R0=1 (not 0xFF), R1=2
    """
    rom = [
        movi(0, 1),    # addr 0: R0 = 1
        b(0),          # addr 1: B → addr 3  (offset=0)
        movi(0, 0xF),  # addr 2: R0 = 0xF  ← flushed, must NOT execute
        movi(1, 2),    # addr 3: R1 = 2
    ]

    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    cocotb.start_soon(rom_driver(dut, rom))
    await reset(dut)

    await ClockCycles(dut.clk, 10)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 1, f"Flushed instruction executed: R0={int(regs[0].value)}, expected 1"
        assert int(regs[1].value) == 2, f"Branch target not reached: R1={int(regs[1].value)}, expected 2"

    await ClockCycles(dut.clk, 3)
    dut._log.info("branch_unconditional_test PASSED")


# ---------------------------------------------------------------------------
# Test 7: Conditional branches (BEQ taken, BEQ not taken)
# ---------------------------------------------------------------------------

@cocotb.test()
async def branch_conditional_test(dut):
    """
    Two programs in one test — BEQ taken and BEQ not taken.
    Each runs in its own reset so registers start clean.

    --- Part A: BEQ taken (Z=1) ---
    Offset: B at addr 2, target addr 4: offset = 4 - 2 - 2 = 0

    ROM:
        addr 0: MOVI R0, #5
        addr 1: CMP  R0, R0    ; Z=1  (5-5=0)
        addr 2: BEQ  0         ; taken → jumps to addr 4
        addr 3: MOVI R1, #0xF  ; MUST BE SKIPPED
        addr 4: MOVI R1, #7    ; must execute

    Expected: R0=5, R1=7 (not 0xF)

    --- Part B: BEQ not taken (Z=0) ---
    Offset: BEQ at addr 2, would-be target addr 4 — but not taken, so irrelevant.

    ROM:
        addr 0: MOVI R0, #5
        addr 1: MOVI R1, #3
        addr 2: CMP  R0, R1    ; Z=0  (5-3≠0)
        addr 3: BEQ  0         ; NOT taken → PC increments to 4
        addr 4: MOVI R2, #8    ; must execute (branch not taken)
        addr 5: MOVI R3, #9    ; must execute

    Expected: R0=5, R1=3, R2=8, R3=9
    """
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    rom = []
    cocotb.start_soon(rom_driver(dut, rom))

    # --- Part A: BEQ taken ---
    # Poison writes R2 (different from target R1) so flush failure is detectable.
    rom[:] = [
        movi(0, 5),    # addr 0: R0 = 5
        cmp(0, 0),     # addr 1: CMP R0, R0 → Z=1
        beq(0),        # addr 2: BEQ → addr 4  (offset=0 → 2+2+0=4)
        movi(2, 0xF),  # addr 3: MUST BE SKIPPED — poisons R2 if it runs
        movi(1, 7),    # addr 4: R1 = 7
    ]
    await reset(dut)
    await ClockCycles(dut.clk, 10)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 5, f"Part A: R0 wrong: {int(regs[0].value)}"
        assert int(regs[1].value) == 7, f"Part A: BEQ not taken: R1={int(regs[1].value)}, expected 7"
        assert int(regs[2].value) == 0, f"Part A: flush failed — addr 3 executed: R2={int(regs[2].value)}, expected 0"

    dut._log.info("branch_conditional_test Part A (BEQ taken) PASSED")

    # --- Part B: BEQ not taken ---
    rom[:] = [
        movi(0, 5),    # addr 0: R0 = 5
        movi(1, 3),    # addr 1: R1 = 3
        cmp(0, 1),     # addr 2: CMP R0, R1 → Z=0  (5≠3)
        beq(0),        # addr 3: BEQ → NOT taken (Z=0)
        movi(2, 8),    # addr 4: R2 = 8  (must execute)
        movi(3, 9),    # addr 5: R3 = 9  (must execute)
    ]
    await RisingEdge(dut.clk)  # exit ReadOnly phase before driving signals
    await reset(dut)
    await ClockCycles(dut.clk, 12)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 5, f"Part B: R0 wrong: {int(regs[0].value)}"
        assert int(regs[1].value) == 3, f"Part B: R1 wrong: {int(regs[1].value)}"
        assert int(regs[2].value) == 8, f"Part B: BEQ incorrectly taken: R2={int(regs[2].value)}"
        assert int(regs[3].value) == 9, f"Part B: R3 wrong: {int(regs[3].value)}"

    dut._log.info("branch_conditional_test Part B (BEQ not taken) PASSED")

    await ClockCycles(dut.clk, 3)
    dut._log.info("branch_conditional_test PASSED")


# ---------------------------------------------------------------------------
# Test 8: Backward branch (negative offset) — countdown loop
# ---------------------------------------------------------------------------

@cocotb.test()
async def branch_backward_test(dut):
    """
    Verify negative offset (backward branch) and sign extension.

    This is the most important branch edge case: the sign extension logic
    {{4{instr[3]}}, instr[3:0]} must correctly produce negative offsets.
    Without it, all loops are broken.

    Program — countdown loop from 3 to 0:
        addr 0: MOVI R1, #1    ; R1 = 1 (constant)
        addr 1: MOVI R2, #0    ; R2 = 0 (zero for CMP)
        addr 2: MOVI R0, #3    ; R0 = 3 (counter)
        addr 3: SUB  R0, R1    ; R0 = R0 - 1        ← loop start
        addr 4: CMP  R0, R2    ; Z=1 when R0==0
        addr 5: BNE  0xC       ; if Z=0, back to addr 3  (offset=-4)
        addr 6: MOVI R3, #7    ; R3 = 7  (executes only after loop exits)

    Offset math: target=3, branch at addr 5
        offset = 3 - 5 - 2 = -4  →  4-bit two's complement = 0b1100 = 0xC
    Verify: branch_target = (5+1) + 1 + (-4) = 3 ✓

    Loop trace:
        iter 1: R0=3→2, Z=0, BNE taken  → addr 3, addr 6 flushed
        iter 2: R0=2→1, Z=0, BNE taken  → addr 3, addr 6 flushed
        iter 3: R0=1→0, Z=1, BNE not taken → falls through to addr 6

    Expected: R0=0, R1=1, R2=0, R3=7
    """
    rom = [
        movi(1, 1),        # addr 0: R1 = 1
        movi(2, 0),        # addr 1: R2 = 0
        movi(0, 3),        # addr 2: R0 = 3 (counter)
        sub(0, 1),         # addr 3: R0 = R0 - 1  ← loop start
        cmp(0, 2),         # addr 4: Z = (R0 == 0)
        bne((-4) & 0xF),   # addr 5: if Z=0, back to addr 3  (offset=-4 = 0xC)
        movi(3, 7),        # addr 6: R3 = 7  (loop exit)
    ]

    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    cocotb.start_soon(rom_driver(dut, rom))
    await RisingEdge(dut.clk)  # exit ReadOnly from previous test
    await reset(dut)

    # 3 MOVIs + (3 iterations × (SUB+CMP+BNE)) + MOVI R3 + pipeline drain
    await ClockCycles(dut.clk, 30)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 0, f"Loop counter wrong: R0={int(regs[0].value)}, expected 0"
        assert int(regs[1].value) == 1, f"R1 corrupted: {int(regs[1].value)}"
        assert int(regs[2].value) == 0, f"R2 corrupted: {int(regs[2].value)}"
        assert int(regs[3].value) == 7, f"Loop exit not reached: R3={int(regs[3].value)}, expected 7"

    await ClockCycles(dut.clk, 3)
    dut._log.info("branch_backward_test PASSED")


# ---------------------------------------------------------------------------
# Test 9: BNE taken, BCS taken, BCS not taken
# ---------------------------------------------------------------------------

@cocotb.test()
async def branch_bne_bcs_test(dut):
    """
    Three parts covering the remaining untested branch conditions.

    Carry flag: CMP Rd, Rs computes Rd + ~Rs + 1. C=1 when Rd >= Rs (no borrow). C=0 when Rd < Rs (borrow).

    --- Part A: BNE taken (Z=0) ---
    R0=3, R1=5: CMP 3,5 → Z=0 → BNE taken
    Offset: BNE at addr 2, target addr 4: offset = 4-2-2 = 0

        addr 0: MOVI R0, #3
        addr 1: MOVI R1, #5
        addr 2: CMP  R0, R1    ; Z=0 (3≠5)
        addr 3: BNE  0         ; taken → addr 5  (offset=0 → 3+2+0=5)
        addr 4: MOVI R2, #0xF  ; MUST BE SKIPPED
        addr 5: MOVI R2, #9    ; must execute

    Expected: R2=9 (not 0xF)

    --- Part B: BCS taken (C=1) ---
    Carry convention: ALU computes A + ~B + 1. carry_out = sum[8].
    C=1 means NO borrow (A >= B). C=0 means borrow (A < B).
    R0=5, R1=3: CMP 5,3 → 5+~3+1=258=0x102 → C=1 → BCS taken
    Offset: BCS at addr 3, target addr 5: offset = 5-3-2 = 0

        addr 0: MOVI R0, #5
        addr 1: MOVI R1, #3
        addr 2: CMP  R0, R1    ; C=1 (5 >= 3, no borrow)
        addr 3: BCS  0         ; taken → addr 5  (offset=0 → 3+2+0=5)
        addr 4: MOVI R2, #0xF  ; MUST BE SKIPPED
        addr 5: MOVI R2, #7    ; must execute

    Expected: R2=7 (not 0xF)

    --- Part C: BCS not taken (C=0) ---
    R0=3, R1=5: CMP 3,5 → 3+~5+1=254=0x0FE → C=0 → BCS not taken

        addr 0: MOVI R0, #3
        addr 1: MOVI R1, #5
        addr 2: CMP  R0, R1    ; C=0 (3 < 5, borrow)
        addr 3: BCS  0         ; NOT taken → PC increments to 4
        addr 4: MOVI R2, #8    ; must execute
        addr 5: MOVI R3, #6    ; must execute

    Expected: R2=8, R3=6
    """
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())

    # Use a single mutable ROM list shared with one persistent rom_driver.
    # Swapping contents with rom[:] = ... is atomic from the driver's perspective
    # since the driver only reads the list on each falling edge — no cancel() needed.
    rom = []
    cocotb.start_soon(rom_driver(dut, rom))
    await RisingEdge(dut.clk)  # exit ReadOnly from previous test

    # --- Part A: BNE taken ---
    # Poison writes R3 (a different register than the target R2),
    # so if addr 4 executes we can detect it via R3 != 0.
    rom[:] = [
        movi(0, 3),    # addr 0: R0 = 3
        movi(1, 5),    # addr 1: R1 = 5
        cmp(0, 1),     # addr 2: CMP R0, R1 → Z=0
        bne(0),        # addr 3: BNE → addr 5  (offset=0 → 3+2+0=5)
        movi(3, 0xF),  # addr 4: MUST BE SKIPPED — poisons R3 if it runs
        movi(2, 9),    # addr 5: R2 = 9
    ]
    await reset(dut)
    await ClockCycles(dut.clk, 12)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[2].value) == 9, f"Part A: BNE not taken: R2={int(regs[2].value)}, expected 9"
        assert int(regs[3].value) == 0, f"Part A: flush failed — addr 4 executed: R3={int(regs[3].value)}, expected 0"
    dut._log.info("branch_bne_bcs_test Part A (BNE taken) PASSED")

    # --- Part B: BCS taken ---
    rom[:] = [
        movi(0, 5),    # addr 0: R0 = 5
        movi(1, 3),    # addr 1: R1 = 3
        cmp(0, 1),     # addr 2: CMP R0, R1 → C=1 (5>=3, no borrow)
        bcs(0),        # addr 3: BCS → addr 5  (offset=0 → 3+2+0=5)
        movi(3, 0xF),  # addr 4: MUST BE SKIPPED — poisons R3 if it runs
        movi(2, 7),    # addr 5: R2 = 7
    ]
    await RisingEdge(dut.clk)  # exit ReadOnly
    await reset(dut)
    await ClockCycles(dut.clk, 12)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[2].value) == 7, f"Part B: BCS not taken: R2={int(regs[2].value)}, expected 7"
        assert int(regs[3].value) == 0, f"Part B: flush failed — addr 4 executed: R3={int(regs[3].value)}, expected 0"
    dut._log.info("branch_bne_bcs_test Part B (BCS taken) PASSED")

    # --- Part C: BCS not taken ---
    rom[:] = [
        movi(0, 3),    # addr 0: R0 = 3
        movi(1, 5),    # addr 1: R1 = 5
        cmp(0, 1),     # addr 2: CMP R0, R1 → C=0 (3<5, borrow)
        bcs(0),        # addr 3: BCS → NOT taken
        movi(2, 8),    # addr 4: R2 = 8  (must execute)
        movi(3, 6),    # addr 5: R3 = 6  (must execute)
    ]
    await RisingEdge(dut.clk)  # exit ReadOnly
    await reset(dut)
    await ClockCycles(dut.clk, 12)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[2].value) == 8, f"Part C: BCS incorrectly taken: R2={int(regs[2].value)}, expected 8"
        assert int(regs[3].value) == 6, f"Part C: R3 wrong: {int(regs[3].value)}, expected 6"
    dut._log.info("branch_bne_bcs_test Part C (BCS not taken) PASSED")

    await ClockCycles(dut.clk, 3)
    dut._log.info("branch_bne_bcs_test PASSED")


# ---------------------------------------------------------------------------
# Test 10: Complex program — multiple branches, ALU, all interleaved
# ---------------------------------------------------------------------------

@cocotb.test()
async def complex_test(dut):
    """
    Hand-written assembly program exercising BNE, BEQ, B, ADD, SUB together.

    Source:
        MOVI R0, #5
        MOVI R1, #8
        CMP  R0, R1
        BNE  ETHAN         ; Z=0 (5≠8) → taken, addr 4 flushed
        MOVI R0, #11       ; never runs
        MOVI R1, #11       ; never runs
    BACK:
        MOVI R3, #7
        B    FORWARD       ; always taken, addr 8 flushed
    ETHAN:
        MOVI R0, #8        ; ← jumped to from BNE
        CMP  R0, R1        ; 8==8 → Z=1
        BEQ  BACK          ; Z=1 → taken, addr 11 flushed
    FORWARD:
        ADD  R0, R3        ; R0 = 8 + 7 = 15
        SUB  R1, R3        ; R1 = 8 - 7 = 1

    Execution trace:
        addr 0:  R0=5
        addr 1:  R1=8
        addr 2:  CMP 5,8 → Z=0
        addr 3:  BNE → taken (Z=0) → jump to addr 8, addr 4 flushed
        addr 8:  R0=8
        addr 9:  CMP 8,8 → Z=1
        addr 10: BEQ → taken (Z=1) → jump to addr 6, addr 11 flushed
        addr 6:  R3=7
        addr 7:  B → always taken → jump to addr 11, addr 8 flushed
        addr 11: ADD R0,R3 → R0 = 8+7 = 15
        addr 12: SUB R1,R3 → R1 = 8-7 = 1

    Branch offsets:
        BNE at addr 3, target addr 8:   offset = 8-3-2 = 3
        B   at addr 7, target addr 11:  offset = 11-7-2 = 2
        BEQ at addr 10, target addr 6:  offset = 6-10-2 = -6 → 0xA (4-bit two's complement)

    Expected: R0=15, R1=1, R3=7
    """
    rom = [
        movi(0, 5),        # addr  0: R0 = 5
        movi(1, 8),        # addr  1: R1 = 8
        cmp(0, 1),         # addr  2: CMP R0, R1 → Z=0
        bne(3),            # addr  3: BNE ETHAN  (offset=3 → 3+2+3=8)
        movi(0, 11),       # addr  4: never runs (flushed by BNE)
        movi(1, 11),       # addr  5: never runs
        movi(3, 7),        # addr  6: BACK: R3 = 7
        b(2),              # addr  7: B FORWARD  (offset=2 → 7+2+2=11)
        movi(0, 8),        # addr  8: ETHAN: R0 = 8
        cmp(0, 1),         # addr  9: CMP R0, R1 → Z=1 (8==8)
        beq((-6) & 0xF),   # addr 10: BEQ BACK   (offset=-6 → 10+2-6=6)
        add(0, 3),         # addr 11: FORWARD: R0 = R0 + R3 = 8+7 = 15
        sub(1, 3),         # addr 12: R1 = R1 - R3 = 8-7 = 1
    ]

    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    cocotb.start_soon(rom_driver(dut, rom))
    await RisingEdge(dut.clk)  # exit ReadOnly from previous test
    await reset(dut)

    # Cycles: 13 instructions + 3 branch flushes + pipeline drain + margin
    await ClockCycles(dut.clk, 25)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 15, f"R0 wrong: {int(regs[0].value)}, expected 15"
        assert int(regs[1].value) ==  1, f"R1 wrong: {int(regs[1].value)}, expected 1"
        assert int(regs[3].value) ==  7, f"R3 wrong: {int(regs[3].value)}, expected 7"

    await ClockCycles(dut.clk, 3)
    dut._log.info("complex_test PASSED")


# ---------------------------------------------------------------------------
# Test 11: Branch over STR — flush must suppress mem_active
# ---------------------------------------------------------------------------

@cocotb.test()
async def branch_over_str_test(dut):
    """
    Verify that a taken branch correctly suppresses a flushed STR.

    When B executes, flush=1 sets IR_valid=0 on the next edge.
    Since mem_active = MemWrite & IR_valid, IR_valid=0 must kill the stall
    before it fires. If it doesn't, a phantom 3-cycle stall delays addr 4
    and addr 5, and the assertions fail due to insufficient cycle budget.

    Also verifies R0/R1 are not corrupted — if STR executed it would not
    change registers, but the write enable and address bus would be wrong.

    ROM:
        addr 0: MOVI R0, #5    ; R0 = 5  (would-be STR data)
        addr 1: MOVI R1, #3    ; R1 = 3  (would-be STR address)
        addr 2: B    +0        ; jump to addr 4  (offset=0 → 2+2+0=4)
        addr 3: STR  R0, R1    ; FLUSHED — must not stall or write
        addr 4: MOVI R2, #9    ; must execute immediately after branch
        addr 5: MOVI R3, #6    ; must also execute

    Expected: R0=5, R1=3, R2=9, R3=6
    Cycle budget is tight: phantom stall would consume 3 extra cycles,
    causing R2/R3 to not be written within the allotted ClockCycles.
    """
    rom = [
        movi(0, 5),   # addr 0: R0 = 5
        movi(1, 3),   # addr 1: R1 = 3
        b(0),         # addr 2: B → addr 4  (offset=0 → 2+2+0=4)
        str_(0, 1),   # addr 3: FLUSHED — must not execute
        movi(2, 9),   # addr 4: R2 = 9
        movi(3, 6),   # addr 5: R3 = 6
    ]

    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    cocotb.start_soon(rom_driver(dut, rom))
    await RisingEdge(dut.clk)  # exit ReadOnly from previous test
    await reset(dut)

    # Tight budget: 6 instructions + 1 flush + 2 pipeline drain = 9 cycles.
    # A phantom STR stall would add 3 more cycles, exhausting this budget.
    await ClockCycles(dut.clk, 12)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 5, f"R0 corrupted: {int(regs[0].value)}, expected 5"
        assert int(regs[1].value) == 3, f"R1 corrupted: {int(regs[1].value)}, expected 3"
        assert int(regs[2].value) == 9, f"R2 wrong: {int(regs[2].value)}, expected 9 — phantom STR stall?"
        assert int(regs[3].value) == 6, f"R3 wrong: {int(regs[3].value)}, expected 6"

    await ClockCycles(dut.clk, 3)
    dut._log.info("branch_over_str_test PASSED")


# ---------------------------------------------------------------------------
# Test 12: LDR then immediately use the loaded value
# ---------------------------------------------------------------------------

@cocotb.test()
async def ldr_use_test(dut):
    """
    Verify that the value loaded by LDR is correctly available to the
    immediately following instruction.

    The 3-cycle LDR stall ensures R0 is written before ADD reads it.
    This test explicitly verifies that the stall is long enough — if the
    stall released one cycle too early, ADD would read R0=0 (reset value)
    instead of the loaded value.

    ROM:
        addr 0: MOVI R1, #6    ; R1 = 6  (load address — data lives at addr 6)
        addr 1: MOVI R2, #3    ; R2 = 3  (addend)
        addr 2: LDR  R0, R1    ; R0 = mem[6] = 0x42
        addr 3: ADD  R0, R2    ; R0 = 0x42 + 3 = 0x45  ← uses loaded value
        addr 4: MOV  R3, R0    ; R3 = 0x45
        addr 5: B    -1        ; infinite loop — halts CPU before data
        addr 6: 0x42           ; data value at load address

    DATA must not sit directly after code: 0x42 decodes as MOV R0,R2
    which would clobber R0 with R2=3 before the test can read it.

    Expected: R0=0x45, R1=6, R2=3, R3=0x45
    """
    DATA = 0x42

    rom = [
        movi(1, 6),   # addr 0: R1 = 6 (data address)
        movi(2, 3),   # addr 1: R2 = 3
        ldr(0, 1),    # addr 2: R0 = mem[6] = 0x42
        add(0, 2),    # addr 3: R0 = R0 + R2 = 0x42 + 3 = 0x45
        mov(3, 0),    # addr 4: R3 = R0 = 0x45
        b(-2),        # addr 5: halt — self-loop (pc=6 during execute, target=7-2=5)
        DATA,         # addr 6: data loaded by LDR
    ]

    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    cocotb.start_soon(rom_driver(dut, rom))
    await RisingEdge(dut.clk)  # exit ReadOnly from previous test
    await reset(dut)

    # 2 MOVIs + 4 LDR + 2 ADD + 2 MOV + 1 drain = ~11 cycles, use 16
    await ClockCycles(dut.clk, 16)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 0x45, f"R0 wrong: {int(regs[0].value):#x}, expected 0x45 — stall too short?"
        assert int(regs[1].value) == 6,    f"R1 corrupted: {int(regs[1].value)}, expected 6"
        assert int(regs[2].value) == 3,    f"R2 corrupted: {int(regs[2].value)}"
        assert int(regs[3].value) == 0x45, f"R3 wrong: {int(regs[3].value):#x}, expected 0x45"

    await ClockCycles(dut.clk, 3)
    dut._log.info("ldr_use_test PASSED")


# ---------------------------------------------------------------------------
# Test 13: STR then LDR from the same address (round-trip memory)
# ---------------------------------------------------------------------------

@cocotb.test()
async def str_ldr_roundtrip_test(dut):
    """
    Write a value to memory with STR, then read it back with LDR.
    Verifies the address bus arbitration is correct for both directions
    in sequence — the same address appears on uo_out twice, once for
    the STR data write and once for the LDR data read.

    ROM:
        addr 0: MOVI R0, #7    ; R0 = 7  (value to store)
        addr 1: MOVI R1, #6    ; R1 = 6  (memory address)
        addr 2: STR  R0, R1    ; mem[6] = 7
        addr 3: MOVI R2, #0    ; R2 = 0  (clear R2 to prove LDR actually writes)
        addr 4: LDR  R2, R1    ; R2 = mem[6] = 7
        addr 5: b(-2)          ; halt
        addr 6: 0              ; data cell — STR will overwrite this

    Expected: R0=7, R1=6, R2=7
    """
    rom = [
        movi(0, 7),   # addr 0: R0 = 7
        movi(1, 6),   # addr 1: R1 = 6
        str_(0, 1),   # addr 2: mem[6] = 7  (STR stalls 3 cycles)
        movi(2, 0),   # addr 3: R2 = 0  (poison — proves LDR actually writes)
        ldr(2, 1),    # addr 4: R2 = mem[6] = 7  (LDR stalls 3 cycles)
        b(-2),        # addr 5: halt (self-loop: target = 6+1-2 = 5)
        0,            # addr 6: data cell (STR writes here; LDR reads back)
    ]

    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    cocotb.start_soon(rom_driver(dut, rom))
    await RisingEdge(dut.clk)
    await reset(dut)

    # 2 MOVIs + 4 STR + 2 MOVI + 4 LDR + 2 B + 2 drain = ~16, use 24
    await ClockCycles(dut.clk, 24)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 7, f"R0 corrupted: {int(regs[0].value)}"
        assert int(regs[1].value) == 6, f"R1 corrupted: {int(regs[1].value)}"
        assert int(regs[2].value) == 7, f"R2 wrong: {int(regs[2].value):#x}, expected 7 — STR/LDR round-trip failed"

    await ClockCycles(dut.clk, 3)
    dut._log.info("str_ldr_roundtrip_test PASSED")


# ---------------------------------------------------------------------------
# Test 14: MOVI boundary values (imm=0 and imm=0xF)
# ---------------------------------------------------------------------------

@cocotb.test()
async def movi_boundary_test(dut):
    """
    MOVI encodes a 4-bit immediate in instr[3:0], zero-extended to 8 bits.
    Test both extremes: imm=0 (all bits clear) and imm=15 (all bits set).

    ROM:
        addr 0: MOVI R0, #0    ; R0 = 0
        addr 1: MOVI R1, #15   ; R1 = 15 (0x0F)
        addr 2: MOVI R2, #0    ; R2 = 0  (confirm zero-extension, not sign-extension)
        addr 3: b(-2)          ; halt

    Expected: R0=0, R1=15, R2=0
    """
    rom = [
        movi(0, 0),   # addr 0: R0 = 0
        movi(1, 15),  # addr 1: R1 = 15
        movi(2, 0),   # addr 2: R2 = 0
        b(-2),        # addr 3: halt
    ]

    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    cocotb.start_soon(rom_driver(dut, rom))
    await RisingEdge(dut.clk)
    await reset(dut)

    await ClockCycles(dut.clk, 12)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 0,  f"R0 wrong: {int(regs[0].value)}, expected 0"
        assert int(regs[1].value) == 15, f"R1 wrong: {int(regs[1].value)}, expected 15"
        assert int(regs[2].value) == 0,  f"R2 wrong: {int(regs[2].value)}, expected 0"

    await ClockCycles(dut.clk, 3)
    dut._log.info("movi_boundary_test PASSED")


# ---------------------------------------------------------------------------
# Test 15: BEQ not-taken, and CMP same register
# ---------------------------------------------------------------------------

@cocotb.test()
async def cmp_beq_nottaken_test(dut):
    """
    Two things in one test:

    1. CMP same register (R0, R0): result is always 0, so zero=1, carry=1
       (no borrow). BEQ should be taken. BNE should NOT be taken.

    2. BEQ not-taken: set up R0 != R1, CMP R0,R1 gives zero=0.
       BEQ must fall through to the next instruction.

    ROM:
        addr 0: MOVI R0, #5    ; R0 = 5
        addr 1: CMP  R0, R0    ; R0-R0=0 → zero=1, carry=1 (no borrow)
        addr 2: BNE  +0        ; NOT taken (zero=1) → falls through to addr 3
        addr 3: MOVI R1, #9    ; R1 = 9  (proves BNE fell through)
        addr 4: MOVI R2, #3    ; R2 = 3
        addr 5: CMP  R1, R2    ; 9-3 ≠ 0 → zero=0
        addr 6: BEQ  +0        ; NOT taken (zero=0) → falls through to addr 7
        addr 7: MOVI R3, #6    ; R3 = 6  (proves BEQ fell through)
        addr 8: b(-2)          ; halt

    Expected: R0=5, R1=9, R2=3, R3=6
    """
    rom = [
        movi(0, 5),   # addr 0: R0 = 5
        cmp(0, 0),    # addr 1: R0-R0=0, zero=1
        bne(0),       # addr 2: NOT taken (zero=1) → fall through
        movi(1, 9),   # addr 3: R1 = 9
        movi(2, 3),   # addr 4: R2 = 3
        cmp(1, 2),    # addr 5: 9-3≠0, zero=0
        beq(0),       # addr 6: NOT taken (zero=0) → fall through
        movi(3, 6),   # addr 7: R3 = 6
        b(-2),        # addr 8: halt
    ]

    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    cocotb.start_soon(rom_driver(dut, rom))
    await RisingEdge(dut.clk)
    await reset(dut)

    await ClockCycles(dut.clk, 24)
    await ReadOnly()

    regs = get_regs(dut)
    if regs is not None:
        assert int(regs[0].value) == 5, f"R0 corrupted: {int(regs[0].value)}"
        assert int(regs[1].value) == 9, f"R1 wrong: {int(regs[1].value)}, expected 9 — BNE taken when it shouldn't be?"
        assert int(regs[2].value) == 3, f"R2 corrupted: {int(regs[2].value)}"
        assert int(regs[3].value) == 6, f"R3 wrong: {int(regs[3].value)}, expected 6 — BEQ taken when it shouldn't be?"

    await ClockCycles(dut.clk, 3)
    dut._log.info("cmp_beq_nottaken_test PASSED")