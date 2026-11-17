$ErrorActionPreference = "Stop"
$dir  = Split-Path -Parent $PSScriptRoot
$bat  = Join-Path $dir "run_scan_quiet.bat"
$name = "SOL Vol Scanner"
$action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $dir
$trigger  = New-ScheduledTaskTrigger -Daily -At 9:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Daily read-only SOL options volatility scan (no orders)." -Force | Out-Null
Write-Host ""
Write-Host "Done. '$name' will run every day at 9:00 am (or as soon as the laptop is next on)."
Write-Host "To run it right now: open Task Scheduler, find '$name', and click Run - or double-click run_scan.bat."
