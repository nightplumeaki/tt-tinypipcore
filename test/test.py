import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

# Instruction encoding helpers
def r_type(ooo, rd, rs):
    return (0b0 << 7) | (ooo << 4) | (rd << 2) | rs

def i_type(rd, imm4):
    return (0b10 << 6) | (rd << 4) | (imm4 & 0xF)

NOP = 0x00  # ADD R0, R0 — harmless

async def reset(dut):
    dut.rst_n.value = 0
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 1)

@cocotb.test()
async def test_str(dut):
    """STR Rd, [Rs]: store Rd value to address in Rs."""
    clock = Clock(dut.clk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    # --- Cycle 0: Feed MOVI R0, 0xA (data to store) ---
    dut.ui_in.value = i_type(0, 0xA)  # R0 = 0x0A
    await ClockCycles(dut.clk, 1)

    # --- Cycle 1: Feed MOVI R1, 0x5 (address) ---
    # Previous MOVI R0 now in execute stage
    dut.ui_in.value = i_type(1, 0x5)  # R1 = 0x05
    await ClockCycles(dut.clk, 1)

    # --- Cycle 2: Feed STR R0, [R1] (ooo=111, rd=00, rs=01) ---
    # Previous MOVI R1 now in execute stage
    dut.ui_in.value = r_type(0b111, 0b00, 0b01)  # STR R0, [R1]
    await ClockCycles(dut.clk, 1)

    # --- Cycle 3: STR is now executing (stall cycle 1) ---
    # addr_out should show address from R1 = 0x05
    # uio_out should show data from R0 = 0x0A
    # uio_oe should be 0xFF (write enable)
    dut.ui_in.value = NOP
    await ClockCycles(dut.clk, 1)

    addr = dut.uo_out.value.integer
    data = dut.uio_out.value.integer
    oe = dut.uio_oe.value.integer

    assert addr == 0x05, f"Expected address 0x05, got {addr:#x}"
    assert data == 0x0A, f"Expected store data 0x0A, got {data:#x}"
    assert oe == 0xFF, f"Expected uio_oe 0xFF, got {oe:#x}"

    dut._log.info("STR test passed: addr=0x%02x data=0x%02x oe=0x%02x", addr, data, oe)
