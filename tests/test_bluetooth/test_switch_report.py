"""Tests for switch_report.py — button encoding, stick encoding, report building."""

import pytest

from gamepadserver.bluetooth.switch_report import (
    encode_buttons,
    encode_stick,
    stick_from_api,
    ReportBuilder,
    parse_output,
    _STICK_CAL,
    LEFT_STICK_CENTER_BYTES,
    RIGHT_STICK_CENTER_BYTES,
)
from gamepadserver.bluetooth.constants import INPUT_FULL, INPUT_SUBCMD_REPLY


# ---------------------------------------------------------------------------
# encode_buttons
# ---------------------------------------------------------------------------

class TestEncodeButtons:

    def test_empty(self):
        assert encode_buttons(set()) == b"\x00\x00\x00"

    def test_single_button(self):
        # A is byte 0, mask 0x08
        result = encode_buttons({"A"})
        assert result == bytes([0x08, 0x00, 0x00])

    def test_multiple_same_byte(self):
        # Y(0x01) + A(0x08) = 0x09 in byte 0
        result = encode_buttons({"Y", "A"})
        assert result[0] == 0x09
        assert result[1] == 0x00
        assert result[2] == 0x00

    def test_across_bytes(self):
        # A in byte 0, HOME in byte 1, DPAD_UP in byte 2
        result = encode_buttons({"A", "HOME", "DPAD_UP"})
        assert result[0] == 0x08  # A
        assert result[1] == 0x10  # HOME
        assert result[2] == 0x02  # DPAD_UP

    def test_unknown_button_ignored(self):
        result = encode_buttons({"A", "NONEXISTENT"})
        assert result == bytes([0x08, 0x00, 0x00])

    def test_all_buttons(self):
        all_buttons = {
            "Y", "X", "B", "A", "R", "ZR",
            "MINUS", "PLUS", "R_STICK", "L_STICK", "HOME", "CAPTURE",
            "DPAD_DOWN", "DPAD_UP", "DPAD_RIGHT", "DPAD_LEFT", "L", "ZL",
        }
        result = encode_buttons(all_buttons)
        assert result[0] == 0xCF  # Y|X|B|A|R|ZR
        assert result[1] == 0x3F  # MINUS|PLUS|R_STICK|L_STICK|HOME|CAPTURE
        assert result[2] == 0xCF  # DPAD_DOWN|UP|RIGHT|LEFT|L|ZL


# ---------------------------------------------------------------------------
# encode_stick
# ---------------------------------------------------------------------------

class TestEncodeStick:

    def test_zero(self):
        result = encode_stick(0, 0)
        assert result == bytes([0x00, 0x00, 0x00])

    def test_max(self):
        result = encode_stick(0xFFF, 0xFFF)
        assert result == bytes([0xFF, 0xFF, 0xFF])

    def test_known_center_left(self):
        # H=0x86F, V=0x77C
        result = encode_stick(0x86F, 0x77C)
        assert result == LEFT_STICK_CENTER_BYTES

    def test_known_center_right(self):
        # H=0x816, V=0x7DD
        result = encode_stick(0x816, 0x7DD)
        assert result == RIGHT_STICK_CENTER_BYTES

    def test_encoding_roundtrip(self):
        """Verify encode → decode gives back original values."""
        h_in, v_in = 0x123, 0x456
        b = encode_stick(h_in, v_in)
        h_out = b[0] | ((b[1] & 0x0F) << 8)
        v_out = (b[2] << 4) | ((b[1] & 0xF0) >> 4)
        assert h_out == h_in
        assert v_out == v_in


# ---------------------------------------------------------------------------
# stick_from_api
# ---------------------------------------------------------------------------

class TestStickFromApi:

    def test_center_maps_to_center_bytes(self):
        assert stick_from_api(0, 0, "left") == LEFT_STICK_CENTER_BYTES
        assert stick_from_api(0, 0, "right") == RIGHT_STICK_CENTER_BYTES

    def test_extremes_within_12bit(self):
        for stick in ("left", "right"):
            for x, y in [(-100, -100), (100, 100), (-100, 100), (100, -100)]:
                data = stick_from_api(x, y, stick)
                assert len(data) == 3
                # Decode and check 12-bit range
                h = data[0] | ((data[1] & 0x0F) << 8)
                v = (data[2] << 4) | ((data[1] & 0xF0) >> 4)
                assert 0 <= h <= 0xFFF
                assert 0 <= v <= 0xFFF

    def test_positive_x_increases_h(self):
        center = stick_from_api(0, 0, "left")
        right = stick_from_api(100, 0, "left")
        h_center = center[0] | ((center[1] & 0x0F) << 8)
        h_right = right[0] | ((right[1] & 0x0F) << 8)
        assert h_right > h_center

    def test_positive_y_increases_v(self):
        center = stick_from_api(0, 0, "left")
        up = stick_from_api(0, 100, "left")
        v_center = (center[2] << 4) | ((center[1] & 0xF0) >> 4)
        v_up = (up[2] << 4) | ((up[1] & 0xF0) >> 4)
        assert v_up > v_center

    def test_clamping(self):
        """Values outside -100..100 should be clamped."""
        max_result = stick_from_api(100, 100, "left")
        over_result = stick_from_api(200, 200, "left")
        assert max_result == over_result


# ---------------------------------------------------------------------------
# ReportBuilder
# ---------------------------------------------------------------------------

class TestReportBuilder:

    def test_standard_report_length(self):
        rb = ReportBuilder()
        report = rb.standard_report()
        assert len(report) == 51  # 0xA1 header + 50 bytes
        assert report[0] == 0xA1
        assert report[1] == INPUT_FULL

    def test_subcommand_report_length(self):
        rb = ReportBuilder()
        report = rb.subcommand_report(0x80, 0x03, b"\x01\x02")
        assert len(report) == 51
        assert report[0] == 0xA1
        assert report[1] == INPUT_SUBCMD_REPLY
        assert report[14] == 0x80  # ACK
        assert report[15] == 0x03  # subcmd ID
        assert report[16] == 0x01  # data[0]
        assert report[17] == 0x02  # data[1]

    def test_timer_increments(self):
        rb = ReportBuilder()
        r1 = rb.standard_report()
        r2 = rb.standard_report()
        assert r2[2] == r1[2] + 1  # timer in byte 1 of report (byte 2 of full)

    def test_timer_wraps(self):
        rb = ReportBuilder()
        rb.timer = 0xFF
        r = rb.standard_report()
        assert r[2] == 0xFF
        r = rb.standard_report()
        assert r[2] == 0x00

    def test_buttons_encoded(self):
        rb = ReportBuilder()
        rb.buttons = {"A"}
        report = rb.standard_report()
        # Button byte 0 is at report offset [3] → full packet [4]
        assert report[4] == 0x08  # A

    def test_empty_report(self):
        rb = ReportBuilder()
        rb.buttons = {"A", "B"}
        empty = rb.empty_report()
        # Empty report should have no buttons
        assert empty[4] == 0x00
        assert empty[5] == 0x00
        assert empty[6] == 0x00
        # Original buttons should be preserved
        assert rb.buttons == {"A", "B"}


# ---------------------------------------------------------------------------
# parse_output
# ---------------------------------------------------------------------------

class TestParseOutput:

    def test_subcommand_report(self):
        # Build a minimal 0x01 output report (rumble + subcommand)
        data = bytearray(15)
        data[0] = 0x01  # report ID
        data[1] = 0x05  # timer
        data[10] = 0x03  # subcommand: set input mode
        result = parse_output(bytes(data))
        assert result is not None
        assert result["report_id"] == 0x01
        assert result["timer"] == 0x05
        assert result["subcommand"] == 0x03

    def test_with_a2_header(self):
        data = bytearray(16)
        data[0] = 0xA2  # HID header
        data[1] = 0x01  # report ID
        data[11] = 0x10  # subcommand: SPI read
        result = parse_output(bytes(data))
        assert result is not None
        assert result["subcommand"] == 0x10

    def test_non_subcommand_returns_none(self):
        data = bytearray(15)
        data[0] = 0x10  # rumble-only report
        assert parse_output(bytes(data)) is None

    def test_too_short_returns_none(self):
        assert parse_output(b"") is None
        assert parse_output(b"\x01\x02") is None
