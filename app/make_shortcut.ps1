# Put a "my-osrm Maps" icon on the desktop that runs app/launch.ps1.
#
#   pwsh -File app/make_shortcut.ps1
#
# Draws app/web/icon.ico (a map pin) on first run with Python + Pillow.

$ErrorActionPreference = 'Stop'
$Root   = Split-Path -Parent $PSScriptRoot
$Pwsh   = (Get-Command pwsh).Source
$Python = Join-Path $env:LOCALAPPDATA 'Python\bin\python.exe'
$Icon   = Join-Path $Root 'app\web\icon.ico'

if (-not (Test-Path $Icon)) {
    & $Python (Join-Path $Root 'app\make_icon.py') $Icon
    if ($LASTEXITCODE) { throw 'icon generation failed' }
}

$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'my-osrm Maps.lnk'
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = $Pwsh
$s.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Root\app\launch.ps1`""
$s.WorkingDirectory = $Root
$s.IconLocation = "$Icon,0"
$s.Description = 'Start Docker, the routing engine and the map app, then open Chrome'
$s.WindowStyle = 1
$s.Save()
"shortcut: $lnk"
