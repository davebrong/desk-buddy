# Install (or update) the Desk Buddy bridge as a Windows login item (the Mac version is install_bridge.sh).
#   DeskBuddyBridge   scheduled task: the Python bridge (Claude Code hooks -> Lump over USB / Wi-Fi),
#                     started at logon and re-checked every 5 minutes, so a crash heals itself.
# Runtime lives in ~/.desk-buddy:  venv\  bridge.py  bridge.log  bridge.out  sounds\
# Chat/Cowork state (the Mac's accessibility helper) isn't ported: Claude Code sessions drive Lump.
#
#   powershell -ExecutionPolicy Bypass -File tools\install_bridge.ps1
#   powershell -ExecutionPolicy Bypass -File tools\install_bridge.ps1 -Uninstall
param([switch]$Uninstall)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RT = Join-Path $HOME ".desk-buddy"
$Task = "DeskBuddyBridge"

if (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Task -Confirm:$false
}
if ($Uninstall) {
    Write-Host "Bridge removed (runtime left in $RT)."
    exit 0
}

New-Item -ItemType Directory -Force -Path $RT | Out-Null
$Py = Join-Path $RT "venv\Scripts\python.exe"
$PyW = Join-Path $RT "venv\Scripts\pythonw.exe"
if (-not (Test-Path $Py)) {
    $sys = Get-Command py -ErrorAction SilentlyContinue
    if ($sys) { & py -3 -m venv (Join-Path $RT "venv") } else { & python -m venv (Join-Path $RT "venv") }
    if (-not (Test-Path $Py)) { throw "Couldn't make a Python venv: install Python 3.10+ from python.org first." }
}
& $Py -m pip -q install --disable-pip-version-check --upgrade pip
& $Py -m pip -q install --disable-pip-version-check pyserial icalendar recurring-ical-events tzdata tzlocal
if ($LASTEXITCODE) { throw "pip install failed" }
Copy-Item (Join-Path $Root "tools\buddy_bridge.py") (Join-Path $RT "bridge.py") -Force
# Lump's voice (WAVs + sounds.json the first time) and the sound effects for his tap gags
& $Py -X utf8 (Join-Path $Root "tools\make_sounds.py")
& $Py -X utf8 (Join-Path $Root "tools\make_gag_sounds.py")

# pythonw: no console window. -X utf8: read the Claude app's files as UTF-8, not the ANSI code page.
$action = New-ScheduledTaskAction -Execute $PyW -Argument "-X utf8 -u `"$(Join-Path $RT 'bridge.py')`"" -WorkingDirectory $RT
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
# relaunch every 5 minutes if it isn't running (a second copy is never started: IgnoreNew)
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Desk Buddy bridge: Claude Code -> Lump" | Out-Null
Start-ScheduledTask -TaskName $Task

Start-Sleep -Seconds 3
try {
    (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:47823/status).Content
    Write-Host "Bridge running. Next: python tools\install_hooks.py"
} catch {
    Write-Host "The bridge didn't answer yet. Look in $RT\bridge.log and $RT\bridge.out."
}
