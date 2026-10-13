# One click: Docker -> routing engine -> map app -> Chrome.
#
# Run by the "my-osrm Maps" desktop shortcut (make it with: pwsh -File app/make_shortcut.ps1).
# Every step checks first and skips what's already running, so clicking it
# twice is harmless. On a problem it says what failed and waits for Enter.

$ErrorActionPreference = 'Stop'
$Root      = Split-Path -Parent $PSScriptRoot
$Docker    = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
$Chrome    = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$PythonW   = Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\pythonw.exe'
$Task      = 'my-osrm map app'
$Engine    = 'na-ferry'                                       # docker-compose service
$EngineUrl = 'http://127.0.0.1:5002/route/v1/driving/-77.0,38.9;-77.0,38.9?overview=false'
$AppUrl    = 'http://127.0.0.1:8080'

$Host.UI.RawUI.WindowTitle = 'my-osrm Maps'

function Step($n, $text) { Write-Host "[$n/4] $text" -ForegroundColor Cyan -NoNewline }
function Ok($text = 'ok') { Write-Host "  $text" -ForegroundColor Green }
function Fail($text) {
    Write-Host "  FAILED" -ForegroundColor Red
    Write-Host "  $text" -ForegroundColor Yellow
    Read-Host "`nPress Enter to close"
    exit 1
}
function Answers($url) {
    try { (Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200 } catch { $false }
}
function WaitFor([scriptblock]$test, [int]$seconds) {
    $t0 = Get-Date
    while (-not (& $test)) {
        if (((Get-Date) - $t0).TotalSeconds -gt $seconds) { return $false }
        Write-Host '.' -NoNewline
        Start-Sleep 2
    }
    $true
}
function DockerUp { docker info *> $null; $LASTEXITCODE -eq 0 }

Write-Host "my-osrm Maps`n" -ForegroundColor White

# 1. Docker Desktop
Step 1 'Docker'
if (DockerUp) { Ok 'already running' } else {
    if (-not (Test-Path $Docker)) { Fail "Docker Desktop not found at $Docker" }
    Start-Process $Docker
    if (WaitFor { DockerUp } 180) { Ok 'started' } else { Fail 'Docker Desktop did not come up in 3 min. Open it and check for errors, then click again.' }
}

# 2. Routing engine (loads ~8 GB; first answer takes about a minute)
Step 2 'Routing engine'
if (Answers $EngineUrl) { Ok 'already running' } else {
    Push-Location $Root
    try { docker compose up -d $Engine *> $null } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { Fail "docker compose up -d $Engine failed. Run it in $Root to see why." }
    if (WaitFor { Answers $EngineUrl } 240) { Ok 'started' } else {
        Fail "The engine didn't answer in 4 min. Check: docker logs --tail 30 my-osrm-$Engine"
    }
}

# 3. Map app (normally already running from the logon task)
Step 3 'Map app'
if (Answers "$AppUrl/api/config") { Ok 'already running' } else {
    if (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue) {
        Start-ScheduledTask -TaskName $Task
    } else {
        Start-Process $PythonW -ArgumentList '-m', 'app' -WorkingDirectory $Root -WindowStyle Hidden
    }
    if (WaitFor { Answers "$AppUrl/api/config" } 60) { Ok 'started' } else {
        Fail "The app didn't answer in 1 min. See $Root\logs\app.log"
    }
}

# 4. Chrome
Step 4 'Opening Chrome'
if (Test-Path $Chrome) { Start-Process $Chrome -ArgumentList '--new-window', $AppUrl } else { Start-Process $AppUrl }
Ok
Start-Sleep 2
