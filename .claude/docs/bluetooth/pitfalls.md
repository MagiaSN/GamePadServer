# Bluetooth HID Emulation: Pitfalls & Lessons Learned

> Documented during Phase 1a implementation (2026-04-14).
> Environment: Raspberry Pi 3B, BlueZ 5.82, Debian 13 (trixie), Python 3.13.
> Switch firmware: current as of 2026-04.

Each section follows: **Symptom** > **Investigation** > **Root cause** >
**Fix** > **Lesson**.

> **Reference capture:** `captures/switch_connect_success.btsnoop`
> is a known-good btmon trace of the full Switch flow (SSP → L2CAP →
> handshake → HOME press → disconnect). When a run fails, diff the
> new trace against this one to localize the divergence. See
> `captures/README.md` for details.

---

## SSP pairing: Agent required

**Symptom:** Switch connects at ACL level, but SSP fails with
Authentication Failure (0x05).  btmon shows `User Confirmation Request
Negative Reply` (0x002d) sent by bluetoothd within <1ms of the
confirmation request.

**Investigation:** Tried `btmgmt --index 0 io-cap 3` to set
NoInputNoOutput at the kernel management level.  The HCI IO Capability
Reply correctly showed 0x03, but bluetoothd still sent a negative
confirmation reply.

**Root cause:** BlueZ 5.82 requires a registered default Agent1 on
D-Bus to handle the `User Confirmation Request`.  When no agent is
registered, `agent_get()` returns NULL internally and bluetoothd sends
a negative reply.  `btmgmt io-cap` sets the kernel-level capability
but does not substitute for a D-Bus agent.

Neither nxbt nor joycontrol register an agent — they were written for
older BlueZ versions where SSP confirmation was handled differently.

**Fix:** Register a D-Bus Agent1 with `NoInputNoOutput` capability,
call `RequestDefaultAgent()`, and run a GLib MainLoop in a daemon
thread so callbacks fire.  The `RequestConfirmation` method simply
returns (no exception = accepted).

**Lesson:** Kernel-level settings (`btmgmt`) and userspace D-Bus
services (`Agent1`) are separate layers.  Setting one does not
guarantee the other behaves correctly.

---

## ReverseServiceDiscovery causes Switch to disconnect

**Symptom:** SSP pairing succeeds (link key exchanged), but the Switch
disconnects the ACL link ~5 seconds later.  The Switch never sends
L2CAP Connection Requests for PSM 17/19.

**Investigation:** btmon showed that after SSP, bluetoothd initiated
an SDP Service Search to the Switch (outgoing L2CAP on PSM 1).  After
the query completed and the SDP channel was torn down, the Switch
disconnected with "Remote User Terminated Connection" (0x13).

**Root cause:** `ReverseServiceDiscovery` defaults to `true` in
`/etc/bluetooth/main.conf`.  This makes bluetoothd do SDP service
discovery on any newly connected device.  A real Pro Controller would
never initiate SDP discovery.  The Switch sees unexpected SDP traffic
from the "controller" and rejects the connection.

This was the most time-consuming issue to diagnose because the SSP
pairing appeared to succeed, and the SDP query in btmon looked like
normal BlueZ housekeeping — it was easy to overlook as the cause of
the subsequent disconnection.

**Fix:** Set `ReverseServiceDiscovery = false` in
`/etc/bluetooth/main.conf` and restart bluetoothd.

**Lesson:** When emulating a device, bluetoothd's "helpful" default
behaviours become hostile.  Always check `main.conf` defaults — a
single setting can silently break the entire flow.

---

## JustWorksRepairing blocks second connection attempt

**Symptom:** First SSP attempt succeeds but the Switch disconnects
(due to ReverseServiceDiscovery, above).  On the second connection
attempt, SSP fails immediately with a negative reply — even though the
Agent is registered and handled the first attempt successfully.

**Investigation:** btmon showed the second `User Confirmation Request`
was rejected in <1ms (0x002d), far too fast for a D-Bus round-trip.
This meant bluetoothd was auto-rejecting without calling the Agent.

**Root cause:** `JustWorksRepairing` defaults to `never`.  After the
first successful pairing (link key stored) and disconnect, the Switch
reconnects and triggers SSP again.  Bluetoothd classifies this as
"repairing" (SSP for an already-bonded device) and auto-rejects
because the policy is `never`.

**Fix:** Set `JustWorksRepairing = always` in `/etc/bluetooth/main.conf`.

**Lesson:** The first and second connection attempts can follow
completely different code paths inside bluetoothd.  When debugging,
always compare the btmon traces of both attempts side by side — don't
assume the second one fails for the same reason.

---

## Handshake timing: 1 Hz then 15 Hz

**Symptom:** L2CAP channels (PSM 17 + 19) connect successfully, but
the Switch disconnects ~1.3 seconds into the handshake.  No
subcommand requests are ever received.

**Investigation:** Compared report sending rate with nxbt
(`controller/protocol.py`) and joycontrol.  Both send standard input
reports at 1-second intervals during the initial validation phase,
switching to ~15 Hz only after receiving the first message from the
Switch.

**Root cause:** Our code sent reports at 15 Hz (every 67ms)
immediately after connection.  During the initial validation window,
the Switch expects slow-rate reports (matching a real controller's
boot-up behaviour).  Receiving reports at 15x the expected rate
triggers a rejection.

**Fix:** Added a `received_first` flag to `handshake()`.  Before the
Switch sends its first message, sleep 1.0s between reports.  After
receiving data, switch to `1/15`s.

**Lesson:** Protocol timing is as important as data format.  The
Switch validates not just *what* the controller sends but *how fast*
it sends it.

---

## Device class gets reset by bluetoothd

**Symptom:** `hciconfig` shows class `0x002508` (Peripheral/Gamepad)
after initial setup, but `btmgmt info` shows `0x6c0000`
(Miscellaneous).  The Switch sometimes doesn't see us as a gamepad.

**Investigation:** btmon showed `Write Class of Device` commands
happening at two points:
1. Our `hciconfig hci0 class 0x002508` — sets correctly
2. bluetoothd's `Add UUID` (for HID service) — resets to `0x6c0000`
   using its own service class bitmap

**Root cause:** When bluetoothd processes `RegisterProfile` or
internally calls `Add UUID`, it recalculates the device class from its
registered service UUIDs and overwrites the HCI-level setting.

**Fix:** Re-apply the device class via `hciconfig` **after**
`RegisterProfile`, multiple times for safety.  The HCI-level class
(visible in btmon during actual connections) is what matters — the
`btmgmt info` output may show a cached/stale value.

**Lesson:** `btmgmt info` and `hciconfig` can report different values
for device class.  Trust btmon (HCI events) over either command's
output.

---

## Dual GLib MainLoop conflict

**Symptom:** D-Bus Agent1 callbacks sometimes don't fire.  The agent
is registered but `RequestConfirmation` is never called.

**Investigation:** Both `agent.py` and `sdp.py` were each starting
their own `GLib.MainLoop().run()` in separate daemon threads.

**Root cause:** Running two `GLib.MainLoop` instances on the same
default GLib context is undefined behaviour.  D-Bus signals may be
dispatched to the wrong loop or not dispatched at all.

**Fix:** Removed the MainLoop from `sdp.py` entirely.  Only
`agent.py` runs a MainLoop.  The SDP module uses `RegisterProfile` to
register the service record but does not need a MainLoop because it
doesn't rely on `NewConnection` callbacks.

**Lesson:** One process, one GLib MainLoop.  If multiple modules need
D-Bus, they must share a single loop.

---

## NewConnection callback unreliable

**Symptom:** `RegisterProfile` with `Role=server` succeeds, the SDP
record is advertised, the Switch connects — but the `NewConnection`
D-Bus callback on our Profile1 object never fires.

**Root cause:** Known BlueZ bug (upstream FS#46687).  The
`NewConnection` callback for `Role=server` profiles is unreliable
across BlueZ versions.

**Fix:** Don't use `NewConnection` at all.  Keep `RegisterProfile`
only for SDP advertisement.  Accept connections via raw L2CAP sockets:
`socket(AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP)` with
bind/listen/accept on PSM 17 + 19.  This is the proven approach used
by both joycontrol and nxbt.

**Lesson:** BlueZ's high-level D-Bus APIs are convenient but
unreliable for HID server use cases.  Use them only where necessary
(SDP registration, Agent) and handle connections at the socket level.

---

## --noplugin=* breaks SDP registration

**Symptom:** With `--noplugin=*`, `RegisterProfile` raises a D-Bus
error.  The HID SDP record is never advertised.

**Root cause:** Some BlueZ core functionality (profile management,
SDP record handling) depends on built-in plugins.  Disabling all
plugins removes too much infrastructure.

**Fix:** Use `--noplugin=input` (disable only the HID input plugin
that intercepts connections).  This preserves SDP registration while
preventing BlueZ from stealing the L2CAP HID connection.

**Lesson:** Be surgical with `--noplugin`.  Disable the minimum set
of plugins that interfere, not everything.

---

## Wrong battery and firmware bytes

**Symptom:** Handshake progresses further than before but still fails
at a later stage (Switch silently drops the connection during SPI
reads).

**Investigation:** Compared our Device Info reply with a real Pro
Controller capture.

**Root cause:** Two incorrect values:
- Battery byte `0x90` (full + charging) — a Bluetooth-connected
  controller should never report charging.  Fixed to `0x80` (full,
  not charging).
- Firmware minor version `0x8B` (3.139) — not a valid firmware
  version.  Fixed to `0x48` (3.72, the standard version).

**Fix:** `BATTERY_FULL = 0x80`, `FW_MINOR = 0x48` in `constants.py`.

**Lesson:** Every byte in the handshake matters.  When something
"almost works," diff each field against a known-good capture.

---

## General principles

1. **bluetoothd is both essential and adversarial.**  It provides
   services we need (SDP registration, SSP agent management) but also
   does things we don't want (SDP discovery, class reset, connection
   interception).  Tame it through config and selective plugin
   disabling.

2. **btmon is the ground truth.**  When `btmgmt`, `hciconfig`, and
   D-Bus APIs disagree, trust the HCI events in btmon.

3. **Compare first vs. second attempts.**  BlueZ internal state
   changes between connection attempts.  The second failure often has a
   different root cause than the first.

4. **Reference implementations are versioned.**  nxbt and joycontrol
   target older BlueZ; their approaches may not work on 5.82+.  Use
   them for protocol understanding, not for BlueZ integration patterns.

5. **Test one variable at a time.**  When multiple issues stack
   (agent + SDP discovery + timing), fixing one may be masked by
   another.  Isolate each failure with btmon before concluding a fix
   doesn't work.
