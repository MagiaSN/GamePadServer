"""Nintendo Switch HID handshake protocol and input loop.

After L2CAP connection is established, the Switch sends a series of
subcommand requests.  This module responds to each, completing the
"pairing handshake" so the controller is recognised and assigned a
player number.

Handshake completion criteria (matches nxbt):
  vibration_enabled AND player_number is set.
"""

from __future__ import annotations

import logging
import time

from .constants import (
    CONTROLLER_PRO,
    FW_MAJOR,
    FW_MINOR,
    NFC_IR_MCU_CONFIG_REPLY,
    SUBCMD_DEVICE_INFO,
    SUBCMD_ENABLE_IMU,
    SUBCMD_ENABLE_VIBRATION,
    SUBCMD_SET_INPUT_MODE,
    SUBCMD_SET_NFC_IR_CONFIG,
    SUBCMD_SET_NFC_IR_STATE,
    SUBCMD_SET_PLAYER_LIGHTS,
    SUBCMD_SET_SHIPMENT,
    SUBCMD_SPI_READ,
    SUBCMD_TRIGGER_BUTTONS,
    spi_read,
)
from .l2cap import L2CAPConnection
from .switch_report import ReportBuilder, parse_output

log = logging.getLogger(__name__)

# Player-light bit patterns → player number
_PLAYER_MAP = {
    0x01: 1, 0x10: 1,
    0x03: 2, 0x30: 2,
    0x07: 3, 0x70: 3,
    0x0F: 4, 0xF0: 4,
}


class SwitchProtocol:
    """Handle the Switch Pro Controller handshake and input sending."""

    def __init__(self, conn: L2CAPConnection, adapter_address: str) -> None:
        self.conn = conn
        self.mac_bytes = self._parse_mac(adapter_address)
        self.report = ReportBuilder()
        self.vibration_enabled = False
        self.player_number: int | None = None

    # ------------------------------------------------------------------
    # Handshake
    # ------------------------------------------------------------------

    def handshake(self, timeout: float = 60.0) -> None:
        """Run the pairing handshake loop until the Switch assigns a player.

        During the initial validation phase the Switch expects reports at
        ~1 Hz.  Once the Switch sends its first message we switch to the
        normal 15 Hz rate.  This matches the behaviour of nxbt and
        joycontrol.

        Raises RuntimeError on timeout.
        """
        deadline = time.monotonic() + timeout
        log.info("Starting handshake…")

        received_first = False

        while time.monotonic() < deadline:
            data = self.conn.recv(128)
            if data:
                if not received_first:
                    received_first = True
                    log.info("Switch sent first message — switching to 15 Hz")
                parsed = parse_output(data)
                if parsed and parsed["subcommand"]:
                    reply = self._handle_subcommand(
                        parsed["subcommand"], parsed["subcmd_data"]
                    )
                    if reply is not None:
                        self.conn.send(reply)
                        time.sleep(1 / 15)
                        continue

            # No subcommand (or unrecognised) — send a standard report
            self.conn.send(self.report.standard_report())
            time.sleep(1.0 if not received_first else 1 / 15)

            if self.vibration_enabled and self.player_number is not None:
                log.info("Handshake complete  player=%d", self.player_number)
                return

        raise RuntimeError("Handshake timed out")

    # ------------------------------------------------------------------
    # Input helpers (for post-handshake use)
    # ------------------------------------------------------------------

    def press_button(self, name: str) -> None:
        """Add a button to the pressed set and send a report."""
        self.report.buttons.add(name)
        self.conn.send(self.report.standard_report())

    def release_button(self, name: str) -> None:
        """Remove a button from the pressed set and send a report."""
        self.report.buttons.discard(name)
        self.conn.send(self.report.standard_report())

    def release_all(self) -> None:
        """Release all buttons and send a report."""
        self.report.buttons.clear()
        self.conn.send(self.report.standard_report())

    def send_idle(self) -> None:
        """Send a standard report with current state (keep-alive)."""
        self.conn.send(self.report.standard_report())

    # ------------------------------------------------------------------
    # Subcommand handlers
    # ------------------------------------------------------------------

    def _handle_subcommand(self, subcmd: int, data: bytes) -> bytes | None:
        handler = self._HANDLERS.get(subcmd)
        if handler is None:
            log.debug("Unknown subcommand 0x%02X, sending ACK 0x80", subcmd)
            return self.report.subcommand_report(0x80, subcmd)
        return handler(self, data)

    def _cmd_device_info(self, _data: bytes) -> bytes:
        log.debug("→ Device Info")
        # Firmware version + controller type + MAC
        payload = bytearray(12)
        payload[0] = FW_MAJOR
        payload[1] = FW_MINOR
        payload[2] = CONTROLLER_PRO  # Pro Controller
        payload[3] = 0x02            # unknown, always 0x02
        payload[4:10] = list(reversed(self.mac_bytes))  # MAC (reversed)
        payload[10] = 0x01           # unknown
        payload[11] = 0x01           # colours in SPI are used
        return self.report.subcommand_report(0x82, SUBCMD_DEVICE_INFO,
                                             bytes(payload))

    def _cmd_set_shipment(self, _data: bytes) -> bytes:
        log.debug("→ Set Shipment State")
        return self.report.subcommand_report(0x80, SUBCMD_SET_SHIPMENT)

    def _cmd_set_input_mode(self, _data: bytes) -> bytes:
        log.debug("→ Set Input Report Mode")
        return self.report.subcommand_report(0x80, SUBCMD_SET_INPUT_MODE)

    def _cmd_trigger_buttons(self, _data: bytes) -> bytes:
        log.debug("→ Trigger Buttons Elapsed Time")
        # 7 x uint16-LE in 10ms units: L, R, ZL, ZR, SL, SR, Home
        payload = bytearray(14)
        # Report ~3s for L and R (pairing indication)
        payload[0:2] = (3000).to_bytes(2, "little")  # L
        payload[2:4] = (3000).to_bytes(2, "little")  # R
        return self.report.subcommand_report(0x83, SUBCMD_TRIGGER_BUTTONS,
                                             bytes(payload))

    def _cmd_spi_read(self, data: bytes) -> bytes:
        if len(data) < 5:
            return self.report.subcommand_report(0x80, SUBCMD_SPI_READ)
        addr = int.from_bytes(data[0:4], "little")
        length = data[4]
        log.debug("→ SPI Read  addr=0x%04X  len=%d", addr, length)
        spi_data = spi_read(addr, length)
        payload = bytearray(5 + length)
        payload[0:4] = data[0:4]   # echo address
        payload[4] = length
        payload[5:5 + length] = spi_data
        return self.report.subcommand_report(0x90, SUBCMD_SPI_READ,
                                             bytes(payload))

    def _cmd_set_player_lights(self, data: bytes) -> bytes:
        if data:
            pattern = data[0]
            self.player_number = _PLAYER_MAP.get(pattern)
            log.info("→ Set Player Lights  pattern=0x%02X  player=%s",
                     pattern, self.player_number)
        return self.report.subcommand_report(0x80, SUBCMD_SET_PLAYER_LIGHTS)

    def _cmd_enable_imu(self, _data: bytes) -> bytes:
        log.debug("→ Enable 6-Axis IMU")
        return self.report.subcommand_report(0x80, SUBCMD_ENABLE_IMU)

    def _cmd_enable_vibration(self, _data: bytes) -> bytes:
        log.info("→ Enable Vibration")
        self.vibration_enabled = True
        return self.report.subcommand_report(0x82, SUBCMD_ENABLE_VIBRATION)

    def _cmd_nfc_ir_config(self, _data: bytes) -> bytes:
        log.debug("→ Set NFC/IR MCU Config")
        return self.report.subcommand_report(0xA0, SUBCMD_SET_NFC_IR_CONFIG,
                                             NFC_IR_MCU_CONFIG_REPLY)

    def _cmd_nfc_ir_state(self, _data: bytes) -> bytes:
        log.debug("→ Set NFC/IR MCU State")
        return self.report.subcommand_report(0x80, SUBCMD_SET_NFC_IR_STATE)

    _HANDLERS = {
        SUBCMD_DEVICE_INFO:       _cmd_device_info,
        SUBCMD_SET_SHIPMENT:      _cmd_set_shipment,
        SUBCMD_SET_INPUT_MODE:    _cmd_set_input_mode,
        SUBCMD_TRIGGER_BUTTONS:   _cmd_trigger_buttons,
        SUBCMD_SPI_READ:          _cmd_spi_read,
        SUBCMD_SET_PLAYER_LIGHTS: _cmd_set_player_lights,
        SUBCMD_ENABLE_IMU:        _cmd_enable_imu,
        SUBCMD_ENABLE_VIBRATION:  _cmd_enable_vibration,
        SUBCMD_SET_NFC_IR_CONFIG: _cmd_nfc_ir_config,
        SUBCMD_SET_NFC_IR_STATE:  _cmd_nfc_ir_state,
    }

    # ------------------------------------------------------------------
    # Util
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_mac(addr: str) -> list[int]:
        """Parse 'AA:BB:CC:DD:EE:FF' into [0xAA, 0xBB, …]."""
        return [int(b, 16) for b in addr.split(":")]
