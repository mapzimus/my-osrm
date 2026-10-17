# Optimal Drives: render a video the safe way (2026-10-14).
#
#   .\experiments\render.ps1 capitals -Sound             # tiktok, 3 parts, ticks & chimes
#   .\experiments\render.ps1 lighthouses -Platform reels -Parts 4   # silent (the default)
#   .\experiments\render.ps1 walmart -Workers 1 -KeepEngine -Cpu   # the old, slow way
#   .\experiments\render.ps1 shoprite -Shorts                      # tiktok, then its 58 s Shorts cut
#   .\experiments\render_queue.ps1 "shoprite -Shorts" "shoprite -Platform reels"   # several, back to back
#
# Why this way: a 65 s reel is ~2,000 frames, beside a 5 GB OSRM container. A single
# long run kept dying from low memory, and a render started as a Claude Code
# background job gets killed by Claude Code when RAM runs short. So:
#   - its own hidden Windows process (survives Claude Code and its memory reaper)
#   - N parts, each a separate python run (memory is released between parts;
#     a crash costs one part, and the parts that finished are kept and skipped)
#   - then --join (stream copy, no re-encode) and the sound mux
# Fast by default (2026-10-14, walmart part 3: 565 s -> 240 s):
#   - the OSRM container is stopped for the render and restarted after; the videos
#     never query it (routes are baked into cache/tour.json). -KeepEngine leaves it up.
#   - up to 6 workers, cut to what the free RAM fits (~2 GB each) when the render starts
#   - frames are encoded on the GPU (reel_kit, NVENC), leaving the CPU to draw them;
#     -Cpu goes back to libx264. Same picture: 48.6 dB PSNR against libx264.
# Progress and the result go to <video>\out\<platform>_render.log; the last line is
# ALL DONE or ... FAILED.
param(
    [Parameter(Mandatory)] [string] $Video,
    [ValidateSet("tiktok", "reels")] [string] $Platform = "tiktok",
    [int] $Parts = 3,
    [int] $Workers = 6,     # a ceiling: lowered to fit the free RAM
    [int] $Only = 0,        # render just this part, no join (trials, benchmarks)
    [switch] $Sound,
    [switch] $KeepEngine,   # leave the OSRM container running during the render
    [switch] $Cpu,          # libx264 instead of the GPU (reel_kit reads OD_ENCODER)
    [switch] $Shorts,       # after the join: also the YouTube Shorts cut (58 s) of this render, format doc section 2
    [switch] $Wait          # run in this window instead of detaching
)
$dir = Join-Path $PSScriptRoot $Video
if (-not (Test-Path "$dir\video.py")) { throw "no video.py in $dir" }
$py = "C:\Users\mhowe\AppData\Local\Python\bin\python.exe"
$log = "$dir\out\${Platform}_render.log"
$engine = "my-osrm-na-ferry"
New-Item -ItemType Directory -Force "$dir\out" | Out-Null

if (-not $Wait) {
    $me = @("-NoProfile", "-File", $PSCommandPath, $Video, "-Platform", $Platform, "-Parts", $Parts,
            "-Workers", $Workers, "-Only", $Only, "-Wait")
    if ($Sound) { $me += "-Sound" }
    if ($KeepEngine) { $me += "-KeepEngine" }
    if ($Cpu) { $me += "-Cpu" }
    if ($Shorts) { $me += "-Shorts" }
    Start-Process pwsh -WindowStyle Hidden -ArgumentList $me
    Write-Output "Rendering $Video ($Platform, $Parts parts, up to $Workers workers) in a hidden window. Log: $log"
    return
}

Set-Location $dir
$enc = if ($Cpu) { "x264" } else { "nvenc" }
"started $(Get-Date -Format 'HH:mm') ($Workers workers, $enc)" | Set-Content $log
$env:OD_ENCODER = $enc

# Free the engine's RAM: stop the container, then drop the Linux page cache so the
# WSL VM hands the memory back to Windows (the OSRM files are memory-mapped and
# stay cached otherwise). Restart afterwards only if it was running before.
$restart = $false
if (-not $KeepEngine) {
    $restart = (docker ps -q -f "name=^$engine$" 2>$null) -ne $null
    if ($restart) {
        docker stop $engine *>> $log
        wsl -d docker-desktop -u root sh -c "sync; echo 3 > /proc/sys/vm/drop_caches" *>> $log
        "engine stopped for the render" | Add-Content $log
    }
}

# Fit the workers to the RAM that is free right now (~2 GB each, 3 GB kept for the
# rest of the machine). Other jobs come and go: on 2026-10-14 a DOK-tagger run took
# 9 GB mid-render and 6 workers drove free RAM to zero.
$freeGB = (Get-Counter '\Memory\Available MBytes').CounterSamples.CookedValue / 1024
$fit = [math]::Max(1, [math]::Floor(($freeGB - 3) / 2))
if ($Workers -gt $fit) {
    "only $([math]::Round($freeGB, 1)) GB free: $Workers workers -> $fit" | Add-Content $log
    $Workers = $fit
}

# The last log line must stay ALL DONE / ... FAILED, so the engine restarts first.
$status, $code = "FAILED (interrupted)", 1
try {
    $range = if ($Only) { @($Only) } else { 1..$Parts }
    foreach ($k in $range) {
        # A part that already finished (from an earlier, interrupted run) is kept.
        $done = Get-ChildItem "out\*_${Platform}_part$k.mp4" -ErrorAction SilentlyContinue |
            Where-Object { (& ffprobe -v error -show_entries format=duration -of csv=p=0 $_.FullName 2>$null) }
        if ($done) { "part $k/$Parts already rendered, skipping" | Add-Content $log; continue }
        & $py video.py --platform $Platform --workers $Workers --part "$k/$Parts" *>> $log
        if ($LASTEXITCODE) { $status = "PART $k FAILED (exit $LASTEXITCODE)"; return }
    }
    if ($Only) { $status, $code = "ALL DONE $(Get-Date -Format 'HH:mm') (part $Only only)", 0; return }
    $join = @("video.py", "--platform", $Platform, "--join", $Parts)
    if ($Sound) { $join += "--sound" }
    & $py @join *>> $log
    if ($LASTEXITCODE) { $status = "JOIN FAILED (exit $LASTEXITCODE)"; return }
    if ($Shorts) {
        # YouTube Shorts: this render sped to 58 s (format doc section 2). The audio
        # filter only applies when the file has a soundtrack.
        $src = "out\${Video}_${Platform}.mp4"
        if (-not (Test-Path $src)) { "-Shorts: $src not found (video.py names its output differently?); skipped" | Add-Content $log }
        else {
            $hasAudio = (& ffprobe -v error -select_streams a -show_entries stream=codec_type -of csv=p=0 $src 2>$null)
            $vcodec = if ($Cpu) { @("-c:v", "libx264", "-preset", "slow", "-crf", "20") } else { @("-c:v", "h264_nvenc", "-preset", "p7", "-cq", "23") }
            $audio = if ($hasAudio) { @("-af", "atempo=65/58", "-c:a", "aac", "-b:a", "192k") } else { @("-an") }
            & ffmpeg -y -loglevel error -i $src -vf "setpts=PTS*58/65" @audio @vcodec -pix_fmt yuv420p -movflags +faststart "out\${Video}_shorts.mp4" *>> $log
            if ($LASTEXITCODE) { $status = "SHORTS FAILED (exit $LASTEXITCODE)"; return }
            "wrote out\${Video}_shorts.mp4" | Add-Content $log
        }
    }
    $status, $code = "ALL DONE $(Get-Date -Format 'HH:mm')", 0
}
finally {
    if ($restart) {
        docker start $engine *>> $log
        "engine restarted" | Add-Content $log
    }
    $status | Add-Content $log
    exit $code
}
