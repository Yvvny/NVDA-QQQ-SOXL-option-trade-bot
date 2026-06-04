param(
    [string]$PythonExe = "python",
    [string]$RepoRoot = "D:\Code\NVDA-QQQ-SOXL-option-trade-bot",
    [string]$SshKey = "C:\Users\26560\.ssh\oci_trading_bot.key",
    [string]$Remote = "ubuntu@137.131.60.215",
    [string]$RemoteRoot = "/opt/trading-bot",
    [string]$LocalRoot = "D:\MarketData\QQQ",
    [switch]$DeleteRemoteAfterVerify = $true
)

$ErrorActionPreference = "Stop"

$logDir = Join-Path $LocalRoot "sync_logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path $LocalRoot | Out-Null

$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$logPath = Join-Path $logDir "qqq_sync_$timestamp.log"

$scriptPath = Join-Path $RepoRoot "tools\sync_qqq_chain_archive.py"
$args = @(
    $scriptPath,
    "--ssh-key", $SshKey,
    "--remote", $Remote,
    "--remote-root", $RemoteRoot,
    "--local-root", $LocalRoot
)

if ($DeleteRemoteAfterVerify) {
    $args += "--delete-remote-after-verify"
}

try {
    "[$(Get-Date -Format o)] Starting QQQ archive sync" | Tee-Object -FilePath $logPath
    & $PythonExe @args *>&1 | ForEach-Object {
        $_ | Out-String | Tee-Object -FilePath $logPath -Append | Out-Null
        $_
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Sync script exited with code $LASTEXITCODE"
    }
    "[$(Get-Date -Format o)] Sync completed successfully" | Tee-Object -FilePath $logPath -Append
}
catch {
    "[$(Get-Date -Format o)] Sync failed: $_" | Tee-Object -FilePath $logPath -Append
    throw
}
