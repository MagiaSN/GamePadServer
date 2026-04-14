"""Tests for bluetooth/constants.py — SPI flash, button map integrity."""

from gamepadserver.bluetooth.constants import (
    BUTTON_MAP,
    SPI_REGIONS,
    spi_read,
    NFC_IR_MCU_CONFIG_REPLY,
)


class TestButtonMap:

    def test_all_entries_have_valid_offset(self):
        for name, (offset, mask) in BUTTON_MAP.items():
            assert offset in (0, 1, 2), f"{name} has invalid offset {offset}"

    def test_all_entries_have_single_bit_mask(self):
        for name, (offset, mask) in BUTTON_MAP.items():
            assert mask & (mask - 1) == 0, f"{name} mask {mask:#x} is not a single bit"

    def test_no_collisions_within_byte(self):
        """No two buttons should share the same (offset, mask)."""
        seen = set()
        for name, entry in BUTTON_MAP.items():
            assert entry not in seen, f"Collision: {name} duplicates {entry}"
            seen.add(entry)


class TestSpiRead:

    def test_known_region(self):
        # Serial number region at 0x6000, length 16
        data = spi_read(0x6000, 16)
        assert len(data) == 16
        assert data == bytes([0xFF] * 16)  # serial is all 0xFF

    def test_colour_region(self):
        data = spi_read(0x6050, 13)
        assert len(data) == 13
        assert data[0:3] == bytes([0x82, 0x82, 0x82])  # body colour

    def test_unknown_region_returns_ff(self):
        data = spi_read(0x0000, 8)
        assert data == bytes([0xFF] * 8)

    def test_partial_overlap(self):
        # Read starting 4 bytes before serial region
        data = spi_read(0x5FFC, 8)
        assert data[:4] == bytes([0xFF] * 4)  # before region
        assert data[4:] == bytes([0xFF] * 4)  # serial (also FF)

    def test_stick_calibration_present(self):
        data = spi_read(0x603D, 18)
        assert len(data) == 18
        # First 3 bytes should be left stick max-above-center
        assert data[0:3] == bytes([0xBA, 0xF5, 0x62])


class TestNfcIrReply:

    def test_length(self):
        assert len(NFC_IR_MCU_CONFIG_REPLY) == 34
