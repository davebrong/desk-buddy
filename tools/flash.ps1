# Build + flash the desk-buddy firmware over a USB data cable (Windows version of flash.sh).
# Day to day, use tools/ota.py instead: it updates him over Wi-Fi, sending only what changed.
# Writes the bootloader, partition table, program (slot app0, made the boot slot) and, unless -NoArt,
# his drawings (firmware/data/lump.bin) into the "lump" partition.
#
#   powershell -ExecutionPolicy Bypass -File tools\flash.ps1               # paced upload (~5 min with drawings)
#   powershell -ExecutionPolicy Bypass -File tools\flash.ps1 -Fast         # normal esptool upload, much quicker
#   options: -NoArt (program only)   -Port COM5 (if it picks the wrong port)
#
# The paced upload (tools/paced_esptool.py) works around macOS dropping bytes on this board's
# CH343 USB-serial chip. Windows' driver may not need it: try -Fast first; if it fails with a
# checksum / "invalid message" error, run again without it.
# Needs Python 3. PlatformIO and esptool are installed into tools\.venv the first time.
param([string]$Port = $env:PORT, [switch]$NoArt, [switch]$Fast)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = if ($env:VENV) { $env:VENV } else { Join-Path $Root "tools\.venv" }
$Py = Join-Path $Venv "Scripts\python.exe"
$Pio = Join-Path $Venv "Scripts\pio.exe"
if (-not (Test-Path $Py)) {
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv $Venv } else { & python -m venv $Venv }
}
if (-not (Test-Path $Pio)) {
    & $Py -m pip -q install --disable-pip-version-check platformio esptool pyserial
    if ($LASTEXITCODE) { throw "pip install failed" }
}

if (-not $Port) {
    # the board's USB-serial: WCH CH343 (VID 1A86) or Espressif's own USB (VID 303A)
    $Port = & $Py -c "from serial.tools import list_ports as l; print(next((p.device for p in l.comports() if p.vid in (0x1A86, 0x303A)), ''))"
}
if (-not $Port) {
    throw "No board found on a COM port. Is it plugged in with a data cable? (Device Manager > Ports should list a USB-SERIAL CH343 or USB Serial Device. If it shows up under Other devices, install WCH's CH343 driver.)"
}
Write-Host "Board on $Port"

# the bridge holds the COM port (Windows won't share it): pause it while flashing, restart it afterwards
$Task = Get-ScheduledTask -TaskName "DeskBuddyBridge" -ErrorAction SilentlyContinue
if ($Task) { Stop-ScheduledTask -TaskName "DeskBuddyBridge"; Start-Sleep -Seconds 1 }
try {
    & $Pio run -d (Join-Path $Root "firmware")
    if ($LASTEXITCODE) { throw "build failed" }
    $B = Join-Path $Root "firmware\.pio\build\buddy"
    $BootApp0 = Join-Path $HOME ".platformio\packages\framework-arduinoespressif32\tools\partitions\boot_app0.bin"
    $images = @("0x0", "$B\bootloader.bin", "0x8000", "$B\partitions.bin", "0xe000", $BootApp0, "0x10000", "$B\firmware.bin")
    if (-not $NoArt) { $images += @("0x410000", (Join-Path $Root "firmware\data\lump.bin")) }   # the "lump" partition
    $write = @("--chip", "esp32s3", "--port", $Port, "--baud", "921600", "write_flash", "--flash_mode", "dio", "--flash_size", "16MB") + $images
    if ($Fast) {
        & $Py -m esptool @write
    } else {
        & $Py -I (Join-Path $Root "tools\paced_esptool.py") --no-stub @write
    }
    if ($LASTEXITCODE) { throw "flashing failed" }
} finally {
    if ($Task) { Start-ScheduledTask -TaskName "DeskBuddyBridge" }
}
