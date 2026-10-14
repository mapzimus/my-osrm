# Optimal Drives: render a video the safe way (2026-10-14).
#
#   .\experiments\render.ps1 capitals -Sound             # tiktok, 3 parts, ticks & chimes
#   .\experiments\render.ps1 lighthouses -Platform reels -Parts 4   # silent (the default)
#
# Why this way: a 65 s reel is ~2,000 frames and 35+ min, beside a 5 GB OSRM container.
# A single long run kept dying from low memory, and a render started as a Claude Code
# background job gets killed by Claude Code when RAM runs short. So:
#   - its own hidden Windows process (survives Claude Code and its memory reaper)
#   - 1 worker (one copy of the basemap in RAM, not two)
#   - N parts, each a separate python run (memory is released between parts;
#     a crash costs one part, and the parts that finished are kept and skipped)
#   - then --join (stream copy, no re-encode) and the sound mux
# Progress and the result go to <video>\out\<platform>_render.log; the last line
# is ALL DONE or ... FAILED.
param(
    [Parameter(Mandatory)] [string] $Video,
    [ValidateSet("tiktok", "reels")] [string] $Platform = "tiktok",
    [int] $Parts = 3,
    [switch] $Sound,
    [switch] $Wait          # run in this window instead of detaching
)
$dir = Join-Path $PSScriptRoot $Video
if (-not (Test-Path "$dir\video.py")) { throw "no video.py in $dir" }
$py = "C:\Users\mhowe\AppData\Local\Python\bin\python.exe"
$log = "$dir\out\${Platform}_render.log"
New-Item -ItemType Directory -Force "$dir\out" | Out-Null

if (-not $Wait) {
    $me = @("-NoProfile", "-File", $PSCommandPath, $Video, "-Platform", $Platform, "-Parts", $Parts, "-Wait")
    if ($Sound) { $me += "-Sound" }
    Start-Process pwsh -WindowStyle Hidden -ArgumentList $me
    Write-Output "Rendering $Video ($Platform, $Parts parts) in a hidden window. Log: $log"
    return
}

Set-Location $dir
"started $(Get-Date -Format 'HH:mm')" | Set-Content $log
for ($k = 1; $k -le $Parts; $k++) {
    # A part that already finished (from an earlier, interrupted run) is kept.
    $done = Get-ChildItem "out\*_${Platform}_part$k.mp4" -ErrorAction SilentlyContinue |
        Where-Object { (& ffprobe -v error -show_entries format=duration -of csv=p=0 $_.FullName 2>$null) }
    if ($done) { "part $k/$Parts already rendered, skipping" | Add-Content $log; continue }
    & $py video.py --platform $Platform --workers 1 --part "$k/$Parts" *>> $log
    if ($LASTEXITCODE) { "PART $k FAILED (exit $LASTEXITCODE)" | Add-Content $log; exit 1 }
}
$join = @("video.py", "--platform", $Platform, "--join", $Parts)
if ($Sound) { $join += "--sound" }
& $py @join *>> $log
if ($LASTEXITCODE) { "JOIN FAILED (exit $LASTEXITCODE)" | Add-Content $log; exit 1 }
"ALL DONE $(Get-Date -Format 'HH:mm')" | Add-Content $log
