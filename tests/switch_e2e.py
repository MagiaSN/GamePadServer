"""End-to-end test: connect to Nintendo Switch via SwitchBackend and press HOME.

Usage:
    sudo .venv/bin/python tests/switch_e2e.py

The Switch must be in "Change Grip/Order" mode (scanning for controllers).
Press Ctrl+C to abort and clean up.

This script exercises the same code path as the production server
(gamepadserver.backends.switch.SwitchBackend) — no duplicated setup logic.
The filename intentionally omits the ``test_`` prefix so pytest does not
collect it: it requires real hardware + root and is meant to be invoked
manually.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from gamepadserver.backends.switch import SwitchBackend

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(name)-36s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("switch_e2e")


async def main() -> None:
    backend = SwitchBackend()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, stop_event.set)

    try:
        log.info("=== Connecting ===")
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
