"""Tests for usb/gadget.py — ConfigFS layout + UDC bind/unbind logic.

ConfigFS isn't available on macOS / non-Pi Linux, so these tests fake
the directory tree under tmp_path and patch the constants so the code
under test interacts with a writeable shadow.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from gamepadserver.usb import gadget as gadget_mod
from gamepadserver.usb.gadget import USBGadget


@pytest.fixture
def fake_configfs(tmp_path, monkeypatch):
    """Stand up a writable shadow of /sys/kernel/config/usb_gadget and
    /sys/class/udc."""
    configfs_root = tmp_path / "usb_gadget"
    udc_dir = tmp_path / "udc"
    configfs_root.mkdir()
    udc_dir.mkdir()
    (udc_dir / "fe980000.usb").mkdir()  # simulate a Pi 4 UDC

    monkeypatch.setattr(gadget_mod, "CONFIGFS_GADGET_ROOT", str(configfs_root))
    monkeypatch.setattr(gadget_mod, "UDC_DIR", str(udc_dir))
    return configfs_root, udc_dir


def test_setup_creates_full_gadget_tree(fake_configfs):
    """setup() should populate every required ConfigFS node."""
    configfs_root, _ = fake_configfs
    gadget = USBGadget()
    with patch.object(gadget_mod, "_ensure_libcomposite"):
        gadget.setup()

    g = configfs_root / "gamepadserver"
    assert (g / "idVendor").read_text() == "0x057e"
    assert (g / "idProduct").read_text() == "0x2009"
    assert (g / "strings/0x409/manufacturer").read_text() == "Nintendo Co., Ltd."
    assert (g / "strings/0x409/product").read_text() == "Pro Controller"
    assert (g / "configs/c.1/MaxPower").read_text() == "500"
    assert (g / "functions/hid.usb0/protocol").read_text() == "0"
    assert (g / "functions/hid.usb0/subclass").read_text() == "0"
    assert (g / "functions/hid.usb0/report_length").read_text() == "64"
    # report_desc is binary
    assert (g / "functions/hid.usb0/report_desc").read_bytes()[:2] == b"\x05\x01"
    # function linked into config
    assert (g / "configs/c.1/hid.usb0").is_symlink()


def test_setup_idempotent(fake_configfs):
    """setup() called twice should not raise (e.g. EEXIST on symlink)."""
    gadget = USBGadget()
    with patch.object(gadget_mod, "_ensure_libcomposite"):
        gadget.setup()
        gadget.setup()


def test_bind_writes_udc(fake_configfs):
    configfs_root, _ = fake_configfs
    gadget = USBGadget()
    with patch.object(gadget_mod, "_ensure_libcomposite"):
        gadget.setup()
    # Pre-create the UDC pseudofile so bind() has somewhere to write
    (configfs_root / "gamepadserver" / "UDC").write_text("")
    gadget.bind()
    assert (configfs_root / "gamepadserver" / "UDC").read_text().strip() == "fe980000.usb"


def test_unbind_clears_udc(fake_configfs):
    configfs_root, _ = fake_configfs
    gadget = USBGadget()
    with patch.object(gadget_mod, "_ensure_libcomposite"):
        gadget.setup()
    (configfs_root / "gamepadserver" / "UDC").write_text("fe980000.usb")
    gadget.unbind()
    assert (configfs_root / "gamepadserver" / "UDC").read_text().strip() == ""


def test_bind_idempotent_when_already_bound_to_same_udc(fake_configfs):
    configfs_root, _ = fake_configfs
    gadget = USBGadget()
    with patch.object(gadget_mod, "_ensure_libcomposite"):
        gadget.setup()
    (configfs_root / "gamepadserver" / "UDC").write_text("fe980000.usb")
    # Should not raise nor try to re-write
    gadget.bind()


def test_bind_refuses_to_overwrite_other_udc(fake_configfs):
    configfs_root, _ = fake_configfs
    gadget = USBGadget()
    with patch.object(gadget_mod, "_ensure_libcomposite"):
        gadget.setup()
    (configfs_root / "gamepadserver" / "UDC").write_text("other-udc.usb")
    with pytest.raises(RuntimeError, match="bound to a different UDC"):
        gadget.bind()


def test_detect_udc_raises_when_none(tmp_path, monkeypatch):
    monkeypatch.setattr(gadget_mod, "UDC_DIR", str(tmp_path / "no-udc"))
    with pytest.raises(RuntimeError, match="No USB Device Controller"):
        gadget_mod.detect_udc()


def test_setup_without_configfs_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gadget_mod, "CONFIGFS_GADGET_ROOT", str(tmp_path / "missing"),
    )
    monkeypatch.setattr(
        gadget_mod, "UDC_DIR", str(tmp_path / "udc-missing"),
    )
    gadget = USBGadget(udc_name="fe980000.usb")
    with patch.object(gadget_mod, "_ensure_libcomposite"):
        with pytest.raises(RuntimeError, match="not found"):
            gadget.setup()


def test_wait_for_hidg_returns_path_when_present(tmp_path, monkeypatch):
    fake = tmp_path / "hidg0"
    fake.write_bytes(b"")
    monkeypatch.setattr(gadget_mod, "HIDG_DEVICE", str(fake))
    gadget = USBGadget(udc_name="fe980000.usb")
    assert gadget.wait_for_hidg(timeout=0.5) == str(fake)


def test_wait_for_hidg_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gadget_mod, "HIDG_DEVICE", str(tmp_path / "never-appears"),
    )
    gadget = USBGadget(udc_name="fe980000.usb")
    with pytest.raises(RuntimeError, match="not available"):
        gadget.wait_for_hidg(timeout=0.1)
