"""Switch Pro Controller HID handshake over USB.

USB enumeration replaces the BT pairing/SDP step, but once
SET_CONFIGURATION completes the host (Switch dock) drives a small
USB-only handshake using report IDs 0x80/0x81 before falling through
to the same 0x01-output / 0x21-input subcommand exchange used over
Bluetooth.

This class extends bluetooth.SwitchProtocol so the post-USB-phase
behaviour is shared with the BT backend — only the 0x80 phase is
new.
"""

from __future__ import annotations

import logging
import time

from gamepadserver.bluetooth.switch_protocol import SwitchProtocol

from .constants import (
    CONTROLLER_PRO_USB_REPLY,
    USB_CMD_BAUDRATE,
    USB_CMD_DISABLE_TIMEOUT,
    USB_CMD_ENABLE_TIMEOUT,
    USB_CMD_HANDSHAKE,
    USB_CMD_RESET,
    USB_CMD_STATUS,
    USB_REPORT_DEV_REPLY,
    USB_REPORT_HOST_CMD,
    USB_USB_PHASE_TIMEOUT_SECONDS,
)

log = logging.getLogger(__name__)


class SwitchUSBProtocol(SwitchProtocol):
    """Switch Pro Controller protocol for the USB transport."""

    def handshake(self, timeout: float | None = None) -> None:
        """Run the full USB handshake.

        Phase 1: respond to 0x80 USB-only commands until the host signals
                 it is done (DISABLE_TIMEOUT) or the per-phase deadline
                 elapses.  Some hosts skip the 0x80 phase entirely and
                 jump straight to 0x01 subcommands — that's also fine.
        Phase 2: standard subcommand handshake (reuses parent class).
        """
        log.info("USB handshake — phase 1 (0x80 commands)")
        self._usb_phase()
        log.info("USB handshake — phase 2 (subcommand)")
        super().handshake(
            timeout=timeout if timeout is not None else 30.0,
        )

    # ------------------------------------------------------------------
    # USB-only 0x80 command phase
    # ------------------------------------------------------------------

    def _usb_phase(self) -> None:
        deadline = time.monotonic() + USB_USB_PHASE_TIMEOUT_SECONDS
        seen_any = False
        while time.monotonic() < deadline:
            data = self.conn.recv(64)
            if not data:
                # Some hosts only send 0x80 commands very briefly; if we
                # see no traffic within ~200 ms after we've already had
                # at least one, assume the phase is over.
                if seen_any:
                    return
                time.sleep(0.01)
                continue
            if data[0] != USB_REPORT_HOST_CMD:
                # Already a subcommand-style report — phase 1 is over.
                # Nothing was consumed except this peeked report; we can't
                # un-read it, so handle it inline as a subcommand.
                self._handle_subcommand_packet(data)
                return
            seen_any = True
            cmd = data[1] if len(data) > 1 else 0
            reply = self._handle_usb_command(cmd, data[2:])
            if reply is not None:
                self.conn.send(reply)
            if cmd == USB_CMD_DISABLE_TIMEOUT:
                # Host has signalled the end of the USB phase.
                return

    def _handle_usb_command(self, cmd: int, _data: bytes) -> bytes | None:
        if cmd == USB_CMD_STATUS:
            log.debug("→ USB status request")
            # Reply payload: padding + controller type + reversed MAC
            payload = bytearray(8)
            payload[0] = 0x00
            payload[1] = CONTROLLER_PRO_USB_REPLY
            payload[2:8] = list(reversed(self.mac_bytes))
            return bytes([USB_REPORT_DEV_REPLY, USB_CMD_STATUS]) + bytes(payload)
        if cmd == USB_CMD_HANDSHAKE:
            log.debug("→ USB handshake")
            return bytes([USB_REPORT_DEV_REPLY, USB_CMD_HANDSHAKE])
        if cmd == USB_CMD_BAUDRATE:
            log.debug("→ USB set baudrate")
            return bytes([USB_REPORT_DEV_REPLY, USB_CMD_BAUDRATE])
        if cmd == USB_CMD_DISABLE_TIMEOUT:
            log.debug("→ USB disable timeout (no reply)")
            return None
        if cmd == USB_CMD_ENABLE_TIMEOUT:
            log.debug("→ USB enable timeout")
            return bytes([USB_REPORT_DEV_REPLY, USB_CMD_ENABLE_TIMEOUT])
        if cmd == USB_CMD_RESET:
            log.debug("→ USB reset (no reply)")
            return None
        log.debug("Unknown USB command 0x%02X", cmd)
        return None

    def _handle_subcommand_packet(self, data: bytes) -> None:
        """Inline handler for a stray 0x01 report seen during phase 1.

        Mirrors what handshake() does on its first iteration so we don't
        silently drop the first subcommand request.
        """
        from gamepadserver.bluetooth.switch_report import parse_output

        parsed = parse_output(data)
        if parsed and parsed["subcommand"]:
            reply = self._handle_subcommand(
                parsed["subcommand"], parsed["subcmd_data"]
            )
            if reply is not None:
                self.conn.send(reply)
