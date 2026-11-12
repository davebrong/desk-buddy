#!/bin/bash
# Build + flash the desk-buddy firmware over a USB data cable, using the paced esptool workaround.
# Day to day, use tools/ota.py instead: it updates him over Wi-Fi, sending only what changed.
# This is for the first install of a new partition layout, or recovery when Wi-Fi updates can't reach him.
# Writes the bootloader, partition table, program (slot app0, made the boot slot) and, unless ART=0,
# his drawings (firmware/data/lump.bin) into the "lump" partition. At this cable's pace the drawings
# take about 5 minutes; ART=0 skips them.
# Needs: PlatformIO, and a Python venv with esptool (set VENV, default tools/.venv).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${VENV:-$ROOT/tools/.venv}"
PORT="${PORT:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)}"
[ -x "$VENV/bin/python" ] || { python3 -m venv "$VENV" && "$VENV/bin/pip" -q install esptool; }
[ -n "$PORT" ] || { echo "No /dev/cu.usbmodem* port found - is the board plugged in with a data cable?"; exit 1; }
# the bridge holds the serial port: pause it while flashing, restart it afterwards
BRIDGE="$HOME/Library/LaunchAgents/com.deskbuddy.bridge.plist"
if [ -f "$BRIDGE" ]; then
  launchctl bootout "gui/$(id -u)/com.deskbuddy.bridge" 2>/dev/null || true
  trap 'launchctl bootstrap "gui/$(id -u)" "$BRIDGE" 2>/dev/null || true' EXIT
  sleep 1
fi
cd "$ROOT/firmware" && pio run
B="$ROOT/firmware/.pio/build/buddy"
BOOT_APP0="$HOME/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin"
ART_ADDR=0x410000                      # the "lump" partition in firmware/partitions_lump.csv
EXTRA=()
[ "${ART:-1}" = "0" ] || EXTRA=("$ART_ADDR" "$ROOT/firmware/data/lump.bin")
"$VENV/bin/python" -I "$ROOT/tools/paced_esptool.py" --chip esp32s3 --port "$PORT" --no-stub --baud 921600 \
  write_flash --flash_mode dio --flash_size 16MB \
  0x0 "$B/bootloader.bin" 0x8000 "$B/partitions.bin" 0xe000 "$BOOT_APP0" 0x10000 "$B/firmware.bin" ${EXTRA[@]+"${EXTRA[@]}"}
