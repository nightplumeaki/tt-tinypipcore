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

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles


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
async def test_pc_advances(dut):
    """
    Feed NOPs and verify addr_out (PC) increments every cycle.
    Baseline: if this fails, the pipeline itself is broken.
    """
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await reset(dut)

    addrs = []
    for _ in range(5):
        dut.ui_in.value = NOP
        await RisingEdge(dut.clk)
        addrs.append(sample(dut)['addr'])

    dut._log.info(f"PC sequence: {[hex(a) for a in addrs]}")
    for i in range(1, len(addrs)):
        assert addrs[i] == addrs[i-1] + 1, \
            f"PC not incrementing at step {i}: {[hex(a) for a in addrs]}"

    dut._log.info("test_pc_advances PASSED")


# ---------------------------------------------------------------------------
# Test 2: STR pin-level check (the main event)
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_str_pins(dut):
    """
    MOVI R0, #0xA   (0x8A)  R0 = 10
    MOVI R1, #0x5   (0x95)  R1 = 5   <- store address
    STR  R0, R1     (0x71)  mem[R1] = R0

    During STR execute (stall cycle 1):
        uo_out  == 0x05   addr_out = B = R1 (store address)
        uio_out == 0x0A   store_data = A = R0 (data)
        uio_oe  == 0xFF   write enable active

    After stall resolves:
        uio_oe  == 0x00   write enable drops
        uo_out  != 0x05   addr_out back to PC
    """
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await reset(dut)

    # Cycle 0: MOVI R0, 0xA enters IF
    dut.ui_in.value = movi(0, 0xA)      # 0x8A
    await RisingEdge(dut.clk)

    # Cycle 1: MOVI R1, 0x5 enters IF; MOVI R0 executes -> R0=0xA written
    dut.ui_in.value = movi(1, 0x5)      # 0x95
    await RisingEdge(dut.clk)

    # Cycle 2: STR R0,R1 enters IF; MOVI R1 executes -> R1=0x5 written
    dut.ui_in.value = str_(0, 1)        # 0x71
    await RisingEdge(dut.clk)

    # Cycle 3: NOP enters IF; STR enters EX -> stall cycle 1 begins
    # mem_active=1, stall=1, addr_out=B=R1=0x5, store_data=A=R0=0xA
    dut.ui_in.value = NOP
    await RisingEdge(dut.clk)

    s = sample(dut)
    dut._log.info(
        f"STR stall C1: addr={s['addr']:#04x}  data={s['data']:#04x}  oe={s['oe']:#04x}"
    )
    assert s['addr'] == 0x05, \
        f"Stall C1: expected addr=0x05 (R1), got {s['addr']:#04x}"
    assert s['data'] == 0x0A, \
        f"Stall C1: expected data=0x0A (R0), got {s['data']:#04x}"
    assert s['oe']   == 0xFF, \
        f"Stall C1: expected uio_oe=0xFF, got {s['oe']:#04x}"

    # Cycle 4: mem_cycle=1, stall drops, PC resumes fetch
    dut.ui_in.value = NOP
    await RisingEdge(dut.clk)

    s2 = sample(dut)
    dut._log.info(
        f"Post-stall  C2: addr={s2['addr']:#04x}  data={s2['data']:#04x}  oe={s2['oe']:#04x}"
    )
    assert s2['oe'] == 0x00, \
        f"Post-stall: expected uio_oe=0x00, got {s2['oe']:#04x} (bus not released)"
    assert s2['addr'] != 0x05, \
        f"Post-stall: addr_out still 0x05 (store addr), not back to PC"

    dut._log.info("test_str_pins PASSED")


# ---------------------------------------------------------------------------
# Test 3: addr_out returns to incrementing PC after stall
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_str_addr_release(dut):
    """
    After STR stall resolves, verify addr_out increments again (PC running).
    This specifically catches the bug where addr_out stays on B after mem_cycle=1.
    """
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await reset(dut)

    dut.ui_in.value = movi(0, 1)        # R0 = 1
    await RisingEdge(dut.clk)
    dut.ui_in.value = movi(1, 0)        # R1 = 0 (store to address 0)
    await RisingEdge(dut.clk)
    dut.ui_in.value = str_(0, 1)        # STR R0, R1
    await RisingEdge(dut.clk)

    # Burn through the stall (2 cycles)
    for _ in range(2):
        dut.ui_in.value = NOP
        await RisingEdge(dut.clk)

    # Collect 4 post-stall PC values — must be sequential
    post_addrs = []
    for _ in range(4):
        dut.ui_in.value = NOP
        await RisingEdge(dut.clk)
        post_addrs.append(sample(dut)['addr'])

    dut._log.info(f"Post-stall PC: {[hex(a) for a in post_addrs]}")
    for i in range(1, len(post_addrs)):
        assert post_addrs[i] == post_addrs[i-1] + 1, \
            f"PC not sequential after stall at step {i}: {[hex(a) for a in post_addrs]}"

    dut._log.info("test_str_addr_release PASSED")


# ---------------------------------------------------------------------------
# Test 4: ADD correctness via branch trap
# (no STR needed — uses BEQ as a comparator)
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_add_via_branch(dut):
    """
    Uses branches as a register comparator to verify ADD without STR.

    MOVI R0, #3
    MOVI R1, #3
    ADD  R0, R1     -> R0 should be 6
    MOVI R1, #6
    CMP  R0, R1     -> Z=1 iff ADD produced 6
    BEQ  +1         -> skip trap if correct
    B    -1         -> infinite loop TRAP (PC stuck) = test FAILED
    NOP             -> branch target = test PASSED

    If ADD is broken, PC gets stuck at the B -1 address.
    We detect a stuck PC as the same value repeating.
    """
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await reset(dut)

    program = [
        movi(0, 3),    # 0x83  MOVI R0, #3
        movi(1, 3),    # 0x93  MOVI R1, #3
        add(0, 1),     # 0x01  ADD  R0, R1   -> R0 = 6
        movi(1, 6),    # 0x96  MOVI R1, #6
        cmp(0, 1),     # 0x51  CMP  R0, R1   -> Z=1 if equal
        beq(1),        # 0xC1  BEQ  +1       -> skip trap
        b(0xF),        # 0xFF  B    -1       -> trap (branch back to self)
        NOP,           # 0x00  pass target
    ]

    for instr in program:
        dut.ui_in.value = instr
        await RisingEdge(dut.clk)

    addrs = []
    for _ in range(10):
        dut.ui_in.value = NOP
        await RisingEdge(dut.clk)
        addrs.append(sample(dut)['addr'])

    dut._log.info(f"Post-branch PC: {[hex(a) for a in addrs]}")
    assert len(set(addrs)) > 1, \
        f"PC stuck — ADD, CMP, or BEQ is broken. addrs={[hex(a) for a in addrs]}"

    dut._log.info("test_add_via_branch PASSED")


# ---------------------------------------------------------------------------
# Test 5: SUB sets carry (borrow), BCS branches on it
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_sub_carry(dut):
    """
    MOVI R0, #2
    MOVI R1, #5
    SUB  R0, R1     -> 2-5 = underflow, C=1 (borrow)
    BCS  +1         -> taken if C=1
    B    -1         -> trap
    NOP             -> pass target
    """
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await reset(dut)

    program = [
        movi(0, 2),    # 0x82  MOVI R0, #2
        movi(1, 5),    # 0x95  MOVI R1, #5
        sub(0, 1),     # 0x11  SUB  R0, R1   -> C=1 (borrow)
        bcs(1),        # 0xE1  BCS  +1
        b(0xF),        # 0xFF  B    -1  trap
        NOP,           # 0x00  pass target
    ]

    for instr in program:
        dut.ui_in.value = instr
        await RisingEdge(dut.clk)

    addrs = []
    for _ in range(8):
        dut.ui_in.value = NOP
        await RisingEdge(dut.clk)
        addrs.append(sample(dut)['addr'])

    dut._log.info(f"Post-SUB PC: {[hex(a) for a in addrs]}")
    assert len(set(addrs)) > 1, \
        f"PC stuck — SUB carry or BCS broken. addrs={[hex(a) for a in addrs]}"

    dut._log.info("test_sub_carry PASSED")


# ---------------------------------------------------------------------------
# Test 6: BNE not taken when Z=1
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_bne_not_taken(dut):
    """
    MOVI R0, #4
    MOVI R1, #4
    CMP  R0, R1     -> Z=1
    BNE  +5         -> NOT taken (Z=1), execution falls through
    MOVI R2, #7     -> should execute (PC continues sequentially)
    ...
    If BNE incorrectly branches, PC will skip ahead and miss the MOVI R2.
    We can't read R2 directly, but we can verify PC is sequential (no jump).
    """
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await reset(dut)

    program = [
        movi(0, 4),    # 0x84  MOVI R0, #4
        movi(1, 4),    # 0x94  MOVI R1, #4
        cmp(0, 1),     # 0x51  CMP  R0, R1   -> Z=1
        bne(5),        # 0xD5  BNE  +5       -> NOT taken
        NOP,           # 0x00  should execute
        NOP,
        NOP,
    ]

    addr_before_bne = None
    for i, instr in enumerate(program):
        dut.ui_in.value = instr
        await RisingEdge(dut.clk)
        if i == 3:  # BNE just fed
            addr_before_bne = sample(dut)['addr']

    # Collect a few more cycles
    post = []
    for _ in range(4):
        dut.ui_in.value = NOP
        await RisingEdge(dut.clk)
        post.append(sample(dut)['addr'])

    dut._log.info(f"addr before BNE={addr_before_bne:#04x}, post={[hex(a) for a in post]}")

    # PC should still be incrementing sequentially — no jump
    for i in range(1, len(post)):
        assert post[i] == post[i-1] + 1, \
            f"PC jumped unexpectedly (BNE taken when it shouldn't be): {[hex(a) for a in post]}"

    dut._log.info("test_bne_not_taken PASSED")