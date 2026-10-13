# Start the offline map app at Windows logon, windowless, and keep it running.
#
#   pwsh -File app/autostart.ps1 install     # register + start now
#   pwsh -File app/autostart.ps1 status      # task state + is the app answering
#   pwsh -File app/autostart.ps1 uninstall   # remove the task (stops the app)
#
# The task runs pythonw -m app (no console window) from the repo root; output
# goes to logs/app.log. A 5-minute repeating trigger restarts it if it dies.
# The routing engine is separate: Docker Desktop starts
# at logon and the na-ferry container has restart: unless-stopped.

param([Parameter(Mandatory)][ValidateSet('install', 'status', 'uninstall')][string]$Action)

$ErrorActionPreference = 'Stop'
$TaskName = 'my-osrm map app'
$Root = Split-Path -Parent $PSScriptRoot
$PythonW = Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\pythonw.exe'
$Url = 'http://127.0.0.1:8080/api/config'

function Answering {
    try { (Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200 } catch { $false }
}

switch ($Action) {
    'install' {
        if (-not (Test-Path $PythonW)) { throw "pythonw not found at $PythonW" }
        $taskAction = New-ScheduledTaskAction -Execute $PythonW -Argument '-m app' -WorkingDirectory $Root
        $logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $logon.Delay = 'PT30S'                           # let Docker Desktop come up first
        # Watchdog: Task Scheduler's restart-on-failure ignores a killed process,
        # so also fire every 5 min. IgnoreNew skips it while the app runs; a
        # second copy would exit anyway (python -m app checks the port first).
        $watch = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger @($logon, $watch) -Settings $settings `
            -Principal $principal -Description 'Offline OSRM map app on http://127.0.0.1:8080 (D:\REPOS\my-osrm\app)' -Force | Out-Null
        if (-not (Answering)) { Start-ScheduledTask -TaskName $TaskName }
        foreach ($i in 1..20) { if (Answering) { break }; Start-Sleep 1 }
        if (Answering) { "installed; app answering on http://127.0.0.1:8080" } else { "installed, but the app isn't answering yet; see logs\app.log" }
    }
    'status' {
        $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $t) { "task: not installed" } else {
            $i = Get-ScheduledTaskInfo -TaskName $TaskName
            "task: $($t.State), last run $($i.LastRunTime), last result $($i.LastTaskResult)"
        }
        "app:  $(if (Answering) { 'answering on http://127.0.0.1:8080' } else { 'NOT answering' })"
    }
    'uninstall' {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
            Where-Object { $_.CommandLine -match '-m app\b' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        "removed"
    }
}
