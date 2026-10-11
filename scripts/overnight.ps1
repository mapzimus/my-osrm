# overnight.ps1 - full rebuild + verification + renders, unattended.
#
#   pwsh -File scripts/overnight.ps1
#
# Each step logs to experiments/isochrone/out/overnight.log and keeps going
# past non-fatal failures; experiments/isochrone/out/OVERNIGHT.md is the
# morning summary (step results, report card, airport snaps, key times).

$ErrorActionPreference = 'Continue'
# 4 extract threads, not 6: peak memory scales with threads, and the 24 GB
# WSL cap froze the machine twice. Build is ~15% slower, and finishes.
$env:OSRM_THREADS = '4'

# Keep Windows from idle-sleeping while this script runs (released when the
# process exits; no power settings are changed). ES_CONTINUOUS|ES_SYSTEM_REQUIRED.
Add-Type -Namespace Win32 -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
[void][Win32.Power]::SetThreadExecutionState([uint32]'0x80000001')
$Root = Split-Path -Parent $PSScriptRoot
$Py = 'C:\Users\mhowe\AppData\Local\Python\bin\python.exe'
$Iso = Join-Path $Root 'experiments\isochrone'
$Out = Join-Path $Iso 'out'
$Log = Join-Path $Out 'overnight.log'
$Summary = Join-Path $Out 'OVERNIGHT.md'
New-Item -ItemType Directory -Force $Out | Out-Null
Set-Location $Root
$results = [ordered]@{}

function Step([string]$name, [scriptblock]$body) {
    $t0 = Get-Date
    "`n===== $name  ($(Get-Date -Format 'HH:mm:ss')) =====" | Tee-Object -FilePath $Log -Append | Out-Host
    $global:LASTEXITCODE = 0
    # Out-Host: step output goes to the log and console, never into this
    # function's return value (which must be just the success flag).
    try { & $body 2>&1 | Tee-Object -FilePath $Log -Append | Out-Host; $ok = ($LASTEXITCODE -eq 0) }
    catch { $_ | Out-String | Tee-Object -FilePath $Log -Append | Out-Host; $ok = $false }
    $mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
    $results[$name] = "$(if ($ok) { 'ok' } else { 'FAILED' }) ($mins min)"
    return $ok
}

"overnight run started $(Get-Date)" | Set-Content $Log

# Every engine off before the build: a running one holds ~3.5 GB of the
# 24 GB WSL cap, and na-ferry restart-loops if its data dir is rebuilt
# underneath it. Raw OSRM output goes to the log (progress lines included);
# only the console is filtered.
docker compose --profile major --profile full stop 2>&1 | Tee-Object -FilePath $Log -Append | Out-Null

$built = Step 'build engine' {
    & $Py scripts\build_ferry_engine.py
}

if ($built) {
    Step 'start engine' {
        docker compose up -d na-ferry
        $t = Get-Date
        do {
            Start-Sleep 5
            try { $r = Invoke-RestMethod 'http://127.0.0.1:5002/route/v1/driving/-77.0,38.9;-77.0,38.9' -TimeoutSec 5 } catch {}
        } until ($r -or ((Get-Date) - $t).TotalMinutes -gt 10)
        if (-not $r) { throw 'engine did not come up within 10 min' }
        "engine ready after $([int]((Get-Date) - $t).TotalSeconds) s"
    } | Out-Null

    Step 'airport snaps' {
        & $Py -c @'
import json, sys, urllib.request
sys.path.insert(0, r"experiments\isochrone")
import flights
bad = 0
for code, (name, lon, lat) in sorted(flights.AIRPORTS.items()):
    with urllib.request.urlopen(f"http://127.0.0.1:5002/nearest/v1/driving/{lon},{lat}", timeout=30) as r:
        d = json.load(r)["waypoints"][0]["distance"]
    flag = "  <-- over 5 km: its island stays blank" if d > 5000 else ""
    bad += d > 5000
    print(f"{code} {name:18s} snaps {d:7.0f} m{flag}")
print(f"{bad} airport(s) over 5 km")
'@
    } | Out-Null

    Step 'report card' {
        & $Py benchmarks\report_card.py --url http://127.0.0.1:5002 --engine na |
            Tee-Object -FilePath (Join-Path $Out 'report_card.txt')
        $global:LASTEXITCODE = 0      # failing trips are findings, not a broken step
    } | Out-Null

    # Grids depend on the engine (new penalties, regions, islands): recompute.
    # The continental bbox changed too, so its land mask goes as well.
    Get-ChildItem (Join-Path $Iso 'cache') -Filter 'boston_*' -ErrorAction SilentlyContinue | Remove-Item -Force
    Remove-Item (Join-Path $Iso 'cache\landmask_continental_0.05.npz') -Force -ErrorAction SilentlyContinue

    foreach ($region in 'regional', 'national', 'continental') {
        Step "web map: $region" {
            Set-Location $Iso
            & $Py isochrone.py --region $region
            Set-Location $Root
        } | Out-Null
    }
    foreach ($fmt in 'feed', 'reel') {
        Step "video: $fmt" {
            Set-Location $Iso
            & $Py animate.py --format $fmt
            Set-Location $Root
        } | Out-Null
    }
}

# Morning summary (written before the commit so it is part of it).
$md = @("# Overnight run - $(Get-Date -Format 'yyyy-MM-dd HH:mm')", '', '| Step | Result |', '|---|---|')
$md += $results.GetEnumerator() | ForEach-Object { "| $($_.Key) | $($_.Value) |" }
$rc = Join-Path $Out 'report_card.txt'
if (Test-Path $rc) { $md += '', '## Report card', '```'; $md += Get-Content $rc; $md += '```' }
$snaps = Select-String -Path $Log -Pattern 'over 5 km' | ForEach-Object Line
if ($snaps) { $md += '', '## Airports that did not snap', ''; $md += $snaps | ForEach-Object { "- $_" } }
$md += '', "Full log: experiments/isochrone/out/overnight.log"
$md | Set-Content $Summary

# Commit the text results only. The mp4/gif/png/html outputs are large and
# regenerated every run; they stay in the working tree for Max to review.
if ($built) {
    Step 'commit results' {
        git add experiments/isochrone/out/report_card.txt experiments/isochrone/out/OVERNIGHT.md
        git commit -q -m "Overnight run: report card and summary`n`nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
        git log --oneline -1
    } | Out-Null
}
"overnight run finished $(Get-Date)" | Tee-Object -FilePath $Log -Append
