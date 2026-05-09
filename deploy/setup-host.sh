#!/usr/bin/env bash
# GamePadServer — host setup script
#
# Applies system-level configuration required for Bluetooth HID emulation.
# Idempotent: safe to run multiple times.
#
# Usage:
#   sudo ./deploy/setup-host.sh
#
# What it does:
#   1. Checks prerequisites (OS, BlueZ, Python, Bluetooth adapter)
#   2. Installs bluetoothd systemd override (--compat --noplugin=input)
#   3. Patches /etc/bluetooth/main.conf (ReverseServiceDiscovery, JustWorksRepairing)
#   4. Restarts bluetoothd
#   5. Verifies the result
#
# See .claude/docs/bluetooth/setup.md for the rationale behind each change.

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${NC}   $*"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $*"; }
info() { echo -e "  [..]  $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHANGES_MADE=0

# ── Root check ───────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (sudo)."
    exit 1
fi

echo "GamePadServer host setup"
echo "========================"
echo

# ── 1. Prerequisites ────────────────────────────────────────────────

echo "1. Checking prerequisites..."

# OS
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    ok "OS: $PRETTY_NAME"
else
    warn "Cannot detect OS"
fi

# BlueZ
if command -v bluetoothctl &>/dev/null; then
    BLUEZ_VER=$(bluetoothctl --version | grep -oP '\d+\.\d+')
    ok "BlueZ: $BLUEZ_VER"
else
    fail "BlueZ not found — install bluez package"
    exit 1
fi

# Python
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
    ok "Python: $PY_VER"
else
    fail "Python 3 not found"
    exit 1
fi

# Bluetooth adapter
if hciconfig hci0 &>/dev/null; then
    BT_ADDR=$(hciconfig hci0 | grep -oP 'BD Address: \K[\da-fA-F:]+')
    ok "Adapter: hci0 ($BT_ADDR)"
else
    fail "No Bluetooth adapter found (hci0)"
    exit 1
fi

# dbus-python
if python3 -c "import dbus" 2>/dev/null; then
    ok "dbus-python: available"
else
    fail "dbus-python not found — install python3-dbus"
    exit 1
fi

echo

# ── 2. bluetoothd systemd override ──────────────────────────────────

echo "2. Installing bluetoothd systemd override..."

OVERRIDE_SRC="$SCRIPT_DIR/etc/systemd/system/bluetooth.service.d/gamepadserver.conf"
OVERRIDE_DST="/etc/systemd/system/bluetooth.service.d/gamepadserver.conf"

# Clean up legacy override file name if present
if [[ -f /etc/systemd/system/bluetooth.service.d/nxbt.conf ]]; then
    rm -f /etc/systemd/system/bluetooth.service.d/nxbt.conf
    info "Removed legacy override (nxbt.conf)"
fi

mkdir -p "$(dirname "$OVERRIDE_DST")"

if [[ -f "$OVERRIDE_DST" ]] && diff -q "$OVERRIDE_SRC" "$OVERRIDE_DST" &>/dev/null; then
    ok "Already installed: $OVERRIDE_DST"
else
    cp "$OVERRIDE_SRC" "$OVERRIDE_DST"
    ok "Installed: $OVERRIDE_DST"
    CHANGES_MADE=1
fi

echo

# ── 3. Patch /etc/bluetooth/main.conf ───────────────────────────────

echo "3. Patching /etc/bluetooth/main.conf..."

MAIN_CONF="/etc/bluetooth/main.conf"

if [[ ! -f "$MAIN_CONF" ]]; then
    fail "$MAIN_CONF not found"
    exit 1
fi

patch_setting() {
    local key="$1"
    local value="$2"
    local comment_pattern="^#${key} = "
    local active_pattern="^${key} = "

    if grep -qP "${active_pattern}${value}$" "$MAIN_CONF"; then
        ok "$key = $value (already set)"
    elif grep -qP "$active_pattern" "$MAIN_CONF"; then
        # Active but wrong value — replace
        sed -i "s|${active_pattern}.*|${key} = ${value}|" "$MAIN_CONF"
        ok "$key = $value (updated)"
        CHANGES_MADE=1
    elif grep -qP "$comment_pattern" "$MAIN_CONF"; then
        # Commented out — uncomment and set
        sed -i "s|${comment_pattern}.*|${key} = ${value}|" "$MAIN_CONF"
        ok "$key = $value (uncommented)"
        CHANGES_MADE=1
    else
        # Not present at all — append under [General]
        sed -i "/^\[General\]/a ${key} = ${value}" "$MAIN_CONF"
        ok "$key = $value (added)"
        CHANGES_MADE=1
    fi
}

# Prevent bluetoothd from doing SDP discovery to connected devices
# (a real controller never does this; the Switch disconnects if it sees it)
patch_setting "ReverseServiceDiscovery" "false"

# Allow SSP re-pairing (default "never" causes auto-rejection on reconnect)
patch_setting "JustWorksRepairing" "always"

echo

# ── 4. Restart bluetoothd ───────────────────────────────────────────

echo "4. Restarting bluetoothd..."

if [[ $CHANGES_MADE -eq 1 ]]; then
    systemctl daemon-reload
    systemctl restart bluetooth
    sleep 1
    if systemctl is-active bluetooth &>/dev/null; then
        ok "bluetoothd restarted successfully"
    else
        fail "bluetoothd failed to start — check: journalctl -u bluetooth"
        exit 1
    fi
else
    ok "No changes made, skipping restart"
fi

echo

# ── 5. USB gadget prerequisites (optional, for SwitchUSBBackend) ─────

echo "5. Configuring USB gadget prerequisites (optional)..."

USB_OK=1

# 5a. Boot config: dwc2 device-tree overlay (Raspberry Pi only).
#     Without this the Pi cannot expose a USB device controller.
BOOT_CONFIG=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [[ -f "$candidate" ]]; then
        BOOT_CONFIG="$candidate"
        break
    fi
done

if [[ -n "$BOOT_CONFIG" ]]; then
    if grep -qE '^\s*dtoverlay=dwc2(,|$)' "$BOOT_CONFIG"; then
        ok "dwc2 overlay already configured in $BOOT_CONFIG"
    else
        echo "" >> "$BOOT_CONFIG"
        echo "# Added by GamePadServer setup-host.sh — enable USB device mode" >> "$BOOT_CONFIG"
        echo "dtoverlay=dwc2,dr_mode=peripheral" >> "$BOOT_CONFIG"
        ok "Added dtoverlay=dwc2,dr_mode=peripheral to $BOOT_CONFIG (reboot required)"
        CHANGES_MADE=1
    fi
else
    warn "No /boot/firmware/config.txt or /boot/config.txt — skipping dwc2 overlay (non-Pi host?)"
    USB_OK=0
fi

# 5b. /etc/modules: dwc2 + libcomposite must load at boot.
MODULES_FILE="/etc/modules"
if [[ -f "$MODULES_FILE" ]]; then
    for mod in dwc2 libcomposite; do
        if grep -qE "^\s*${mod}\s*$" "$MODULES_FILE"; then
            ok "$mod already in $MODULES_FILE"
        else
            echo "$mod" >> "$MODULES_FILE"
            ok "Added $mod to $MODULES_FILE"
            CHANGES_MADE=1
        fi
    done
else
    warn "$MODULES_FILE not found — load dwc2 and libcomposite manually for USB mode"
    USB_OK=0
fi

# 5c. Try modprobe libcomposite for the current session — non-fatal.
if modprobe libcomposite 2>/dev/null; then
    ok "libcomposite module loaded"
else
    warn "Could not load libcomposite (will be loaded after reboot if dwc2 enabled)"
fi

if [[ $USB_OK -eq 1 ]]; then
    info "USB gadget mode enabled.  Reboot if dwc2 was newly added."
else
    info "USB backend prerequisites not configured (Bluetooth backend still works)."
fi

echo

# ── 6. Verify ───────────────────────────────────────────────────────

echo "6. Verifying configuration..."

ERRORS=0

# Check override is loaded
EXEC_LINE=$(systemctl show bluetooth -p ExecStart 2>/dev/null)
if echo "$EXEC_LINE" | grep -q -- "--compat" && echo "$EXEC_LINE" | grep -q -- "--noplugin=input"; then
    ok "bluetoothd flags: --compat --noplugin=input"
else
    fail "bluetoothd override not applied"
    ERRORS=$((ERRORS + 1))
fi

# Check main.conf
if grep -qP "^ReverseServiceDiscovery = false" "$MAIN_CONF"; then
    ok "ReverseServiceDiscovery = false"
else
    fail "ReverseServiceDiscovery not set"
    ERRORS=$((ERRORS + 1))
fi

if grep -qP "^JustWorksRepairing = always" "$MAIN_CONF"; then
    ok "JustWorksRepairing = always"
else
    fail "JustWorksRepairing not set"
    ERRORS=$((ERRORS + 1))
fi

# Check adapter is up
if hciconfig hci0 | grep -q "UP RUNNING"; then
    ok "hci0 is UP"
else
    warn "hci0 is not UP (will be brought up at runtime)"
fi

echo
if [[ $ERRORS -eq 0 ]]; then
    echo -e "${GREEN}Host setup complete.${NC}"
    echo "You can now run: sudo .venv/bin/python -m gamepadserver"
else
    echo -e "${RED}Setup completed with $ERRORS error(s). Review the output above.${NC}"
    exit 1
fi
