# Optimal Drives: render several videos back to back, in one hidden process (2026-10-17).
#
#   .\experiments\render_queue.ps1 "shoprite" "shoprite -Platform reels -Shorts" "aw -Sound"
#
# Each argument is one render.ps1 command line (without the script name). render.ps1 ends
# with `exit`, so `& render.ps1 a; & render.ps1 b` in one pwsh never runs b; this script runs
# each as its own pwsh process and waits. Progress: experiments\render_queue.log, plus each
# video's own out\<platform>_render.log. A failed render does not stop the queue.
param(
    [Parameter(Mandatory, ValueFromRemainingArguments)] [string[]] $Jobs,
    [switch] $Wait          # run in this window instead of detaching
)
$log = Join-Path $PSScriptRoot "render_queue.log"
if (-not $Wait) {
    $me = @("-NoProfile", "-File", $PSCommandPath, "-Wait") + $Jobs
    Start-Process pwsh -WindowStyle Hidden -ArgumentList $me
    Write-Output "Queued $($Jobs.Count) render(s) in a hidden window. Log: $log"
    return
}
"queue started $(Get-Date -Format 'yyyy-MM-dd HH:mm'): $($Jobs.Count) job(s)" | Set-Content $log
$failed = 0
foreach ($job in $Jobs) {
    $argv = $job -split '\s+' | Where-Object { $_ }
    "[$(Get-Date -Format 'HH:mm')] start: render.ps1 $job" | Add-Content $log
    & pwsh -NoProfile -File (Join-Path $PSScriptRoot "render.ps1") @argv -Wait
    $code = $LASTEXITCODE
    if ($code) { $failed++ }
    "[$(Get-Date -Format 'HH:mm')] $(if ($code) { "FAILED (exit $code)" } else { 'done' }): $job" | Add-Content $log
}
"queue finished $(Get-Date -Format 'HH:mm'): $($Jobs.Count - $failed) ok, $failed failed" | Add-Content $log
exit $failed
