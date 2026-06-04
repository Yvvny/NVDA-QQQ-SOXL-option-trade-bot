param(
    [string]$TaskName = "QQQ Full Chain Sync",
    [string]$RepoRoot = "D:\Code\NVDA-QQQ-SOXL-option-trade-bot",
    [string]$PowerShellExe = "powershell.exe",
    [string]$PythonExe = "python",
    [string]$SshKey = "C:\Users\26560\.ssh\oci_trading_bot.key",
    [string]$Remote = "ubuntu@137.131.60.215",
    [string]$RemoteRoot = "/opt/trading-bot",
    [string]$LocalRoot = "D:\MarketData\QQQ"
)

$ErrorActionPreference = "Stop"

$syncScript = Join-Path $RepoRoot "tools\run_qqq_chain_sync.ps1"
if (-not (Test-Path $syncScript)) {
    throw "Sync wrapper script not found: $syncScript"
}

$escapedArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$syncScript`"",
    "-PythonExe", "`"$PythonExe`"",
    "-RepoRoot", "`"$RepoRoot`"",
    "-SshKey", "`"$SshKey`"",
    "-Remote", "`"$Remote`"",
    "-RemoteRoot", "`"$RemoteRoot`"",
    "-LocalRoot", "`"$LocalRoot`""
) -join " "

$action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $escapedArgs
$trigger = New-ScheduledTaskTrigger -Daily -At 8:00PM
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Pull QQQ full-chain archive from cloud, verify files, gzip locally, and delete verified remote raw files." `
    | Out-Null

Write-Output "Installed scheduled task: $TaskName"
