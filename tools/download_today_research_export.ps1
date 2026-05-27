param(
    [string]$Server = "137.131.60.215",
    [string]$RemoteUser = "ubuntu",
    [string]$KeyPath = "$HOME\.ssh\oci_trading_bot.key",
    [string]$RemoteAppDir = "/opt/trading-bot",
    [string]$Date = "",
    [string]$OutputDir = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Get-NewYorkDateString {
    try {
        $tz = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
    }
    catch {
        $tz = [System.TimeZoneInfo]::FindSystemTimeZoneById("America/New_York")
    }

    return [System.TimeZoneInfo]::ConvertTime([System.DateTimeOffset]::Now, $tz).ToString("yyyy-MM-dd")
}

if ([string]::IsNullOrWhiteSpace($Date)) {
    $Date = Get-NewYorkDateString
}

if ($Date -notmatch '^\d{4}-\d{2}-\d{2}$') {
    throw "Date must use yyyy-MM-dd, for example 2026-05-19."
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $repoRoot "docs\reports\research"
}

$fileName = "chatgpt_export_$Date.md"
$remotePath = "$RemoteAppDir/docs/reports/research/$fileName"
$remoteSpec = "${RemoteUser}@${Server}:$remotePath"
$localPath = Join-Path $OutputDir $fileName

Write-Host "Downloading: $remoteSpec"
Write-Host "Destination: $localPath"

if ($DryRun) {
    Write-Host "Dry run only. No file downloaded."
    exit 0
}

if (-not (Test-Path -LiteralPath $KeyPath)) {
    throw "SSH key not found: $KeyPath"
}

if (-not (Get-Command scp -ErrorAction SilentlyContinue)) {
    throw "scp is not available in PATH. Install OpenSSH Client or run this from a terminal that has scp."
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

& scp -i $KeyPath $remoteSpec $OutputDir

if ($LASTEXITCODE -ne 0) {
    throw "scp failed with exit code $LASTEXITCODE. Confirm the file exists on the server and run this script from local PowerShell, not inside SSH."
}

Write-Host "Downloaded: $localPath"
