"""Tests for the USB-only 0x80 handshake phase."""

from __future__ import annotations

from collections import deque

import pytest

from gamepadserver.usb.constants import (
    CONTROLLER_PRO_USB_REPLY,
    USB_CMD_DISABLE_TIMEOUT,
    USB_CMD_HANDSHAKE,
    USB_CMD_STATUS,
    USB_REPORT_DEV_REPLY,
    USB_REPORT_HOST_CMD,
)
from gamepadserver.usb.switch_protocol import SwitchUSBProtocol


class FakeConn:
    """Stand-in for HIDGDevice — drains a queue of host commands and
    records the controller's replies."""

    def __init__(self, incoming: list[bytes]) -> None:
        self.incoming = deque(incoming)
        self.sent: list[bytes] = []

    def recv(self, _max_size: int = 64) -> bytes:
        if self.incoming:
            return self.incoming.popleft()
        return b""

    def send(self, data: bytes) -> None:
        self.sent.append(data)


def _host(cmd: int, *payload: int) -> bytes:
    return bytes([USB_REPORT_HOST_CMD, cmd, *payload])


def test_status_request_reply_includes_pro_controller_id_and_mac():
    fake = FakeConn([_host(USB_CMD_STATUS), _host(USB_CMD_DISABLE_TIMEOUT)])
    proto = SwitchUSBProtocol(fake, "00:17:AB:00:00:01")
    proto._usb_phase()

    # First reply = status, second = (none, since disable_timeout has no reply)
    assert len(fake.sent) >= 1
    reply = fake.sent[0]
    assert reply[0] == USB_REPORT_DEV_REPLY
    assert reply[1] == USB_CMD_STATUS
    # Payload: pad, controller-type, reversed-MAC
    assert reply[3] == CONTROLLER_PRO_USB_REPLY
    assert reply[4:10] == bytes([0x01, 0x00, 0x00, 0xAB, 0x17, 0x00])


def test_handshake_command_acked():
    fake = FakeConn([_host(USB_CMD_HANDSHAKE), _host(USB_CMD_DISABLE_TIMEOUT)])
    proto = SwitchUSBProtocol(fake, "00:00:00:00:00:00")
    proto._usb_phase()

    assert fake.sent[0] == bytes([USB_REPORT_DEV_REPLY, USB_CMD_HANDSHAKE])


def test_disable_timeout_ends_phase_without_reply():
    fake = FakeConn([_host(USB_CMD_DISABLE_TIMEOUT)])
    proto = SwitchUSBProtocol(fake, "00:00:00:00:00:00")
    proto._usb_phase()

    # No reply for 0x80 0x04
    assert fake.sent == []


def test_unknown_command_ignored():
    fake = FakeConn([_host(0x99), _host(USB_CMD_DISABLE_TIMEOUT)])
    proto = SwitchUSBProtocol(fake, "00:00:00:00:00:00")
    proto._usb_phase()
    # 0x99 produced no reply, 0x04 ends phase silently
    assert fake.sent == []


def test_phase_exits_when_no_traffic(monkeypatch):
    """If the host never sends 0x80 commands the phase should still
    return after the per-phase deadline (the BT-style subcommand
    handshake then takes over)."""
    import time

    from gamepadserver.usb import switch_protocol as sp_mod

    # Tighten the deadline so the test isn't slow.
    monkeypatch.setattr(sp_mod, "USB_USB_PHASE_TIMEOUT_SECONDS", 0.1)

    fake = FakeConn([])
    proto = SwitchUSBProtocol(fake, "00:00:00:00:00:00")
    start = time.monotonic()
    proto._usb_phase()
    elapsed = time.monotonic() - start
    assert elapsed <= 1.0
    assert fake.sent == []


def test_subcommand_packet_during_usb_phase_handled_inline():
    """Some hosts skip 0x80 entirely and start with a 0x01 report.
    The protocol must still respond instead of dropping it."""
    # Build a minimal 0x01 report with subcommand SET_SHIPMENT (0x08)
    # that parse_output() recognises.
    rumble_subcmd = bytearray(64)
    rumble_subcmd[0] = 0x01           # report id
    rumble_subcmd[10] = 0x08          # subcommand id (set shipment)

    fake = FakeConn([bytes(rumble_subcmd)])
    proto = SwitchUSBProtocol(fake, "00:00:00:00:00:00")
    proto._usb_phase()

    assert len(fake.sent) == 1
    # Reply is a 0x21 subcommand-reply report (0xA1 prefix included —
    # the HIDG transport layer strips it before write())
    assert fake.sent[0][0] == 0xA1
    assert fake.sent[0][1] == 0x21
