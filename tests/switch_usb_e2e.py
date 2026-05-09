"""End-to-end test: connect to a Switch dock over USB and press HOME.

Usage:
    sudo .venv/bin/python tests/switch_usb_e2e.py

Hardware setup:
    1. Pi 4 / Pi Zero 2W with USB device-mode capable port (USB-C / micro-USB).
       Pi 5 is *not* supported — its USB-C port is power-only (no peripheral mode).
    2. dwc2 overlay enabled — see deploy/setup-host.sh.
    3. USB cable from the Pi's gadget port to the Switch dock USB-A.
    4. Switch is powered on, on a screen that accepts USB controllers
       (Home menu or any game).  No "Change Grip/Order" needed for USB.

This script exercises the same code path as the production server
(gamepadserver.backends.switch_usb.SwitchUSBBackend).  The filename
intentionally omits the ``test_`` prefix so pytest does not collect it.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from gamepadserver.backends.switch_usb import SwitchUSBBackend

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(name)-36s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("switch_usb_e2e")


async def main() -> None:
    backend = SwitchUSBBackend()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, stop_event.set)

    try:
        log.info("=== Soft-attach (UDC bind) ===")
        await backend.connect()

        log.info("=== Pressing HOME for 0.5 s ===")
        await backend.press_buttons(["HOME"], duration=0.5)

        log.info("=== Keeping connection alive for 3 s (Ctrl+C to abort) ===")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

        log.info("=== Done ===")
    finally:
        try:
            await backend.disconnect()
        except Exception as exc:
            log.warning("Disconnect raised: %s", exc)


if __name__ == "__main__":
    asyncio.run(main())
