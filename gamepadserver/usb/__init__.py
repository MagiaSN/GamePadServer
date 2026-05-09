"""Self-implemented USB gadget HID stack for game controller emulation.

Mirror of the bluetooth/ module but using Linux USB Gadget (ConfigFS +
/dev/hidgN) instead of L2CAP sockets.  Same input report encoding —
only the transport layer differs.
"""
