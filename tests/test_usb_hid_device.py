"""Tests for usb/hid_device.py — verifies the BT-header stripping
contract that lets SwitchProtocol be reused across BT and USB."""

from __future__ import annotations

import os

from gamepadserver.usb.hid_device import HIDGDevice


def _make_pipe_device() -> tuple[HIDGDevice, int]:
    """Build a HIDGDevice backed by a pipe so we can inspect bytes
    that would have gone over the wire to /dev/hidg0."""
    rfd, wfd = os.pipe()
    dev = HIDGDevice.__new__(HIDGDevice)
    dev._fd = wfd
    dev._path = "<pipe>"
    return dev, rfd


def test_send_strips_a1_input_header():
    dev, rfd = _make_pipe_device()
    try:
        dev.send(bytes([0xA1, 0x30, 0x00, 0x99]))
        out = os.read(rfd, 64)
        assert out == bytes([0x30, 0x00, 0x99])
    finally:
        os.close(rfd)
        dev.close()


def test_send_strips_a2_output_header():
    dev, rfd = _make_pipe_device()
    try:
        dev.send(bytes([0xA2, 0x10, 0x42]))
        out = os.read(rfd, 64)
        assert out == bytes([0x10, 0x42])
    finally:
        os.close(rfd)
        dev.close()


def test_send_passes_through_when_no_header():
    dev, rfd = _make_pipe_device()
    try:
        dev.send(bytes([0x30, 0x01, 0x02]))
        out = os.read(rfd, 64)
        assert out == bytes([0x30, 0x01, 0x02])
    finally:
        os.close(rfd)
        dev.close()


def test_send_empty_is_noop():
    dev, _ = _make_pipe_device()
    dev.send(b"")  # Must not raise


def test_recv_returns_empty_when_no_data():
    """Non-blocking recv() should return b'' rather than blocking."""
    rfd, wfd = os.pipe()
    os.set_blocking(rfd, False)
    dev = HIDGDevice.__new__(HIDGDevice)
    dev._fd = rfd
    dev._path = "<pipe>"
    try:
        assert dev.recv() == b""
    finally:
        os.close(wfd)
        dev.close()


def test_recv_returns_data_when_present():
    rfd, wfd = os.pipe()
    os.set_blocking(rfd, False)
    dev = HIDGDevice.__new__(HIDGDevice)
    dev._fd = rfd
    dev._path = "<pipe>"
    try:
        os.write(wfd, bytes([0x01, 0x02, 0x03]))
        assert dev.recv() == bytes([0x01, 0x02, 0x03])
    finally:
        os.close(wfd)
        dev.close()
