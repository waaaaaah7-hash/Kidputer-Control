#Requires -Version 5.1
<#
.SYNOPSIS
    Remove the Kidputer Control agent. Run from an elevated PowerShell.

.DESCRIPTION
    Reports the uninstall to the cloud (best effort, so the dashboard shows it), removes the watchdog
    task, stops the watchdog and UI agent, and deletes the program and data folders.
    Afterwards, delete the device in the dashboard to revoke its token.
#>
[CmdletBinding()]
param(
    # Keep %ProgramData%\KidputerControl\logs.
    [switch]$KeepLogs
)

$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated (Run as administrator) PowerShell.'
}

$installRoot = Join-Path $env:ProgramFiles 'KidputerControl'
$dataRoot = Join-Path $env:ProgramData 'KidputerControl'
$taskPath = '\KidputerControl\'
$taskName = 'Watchdog'

$module = Join-Path $installRoot 'Modules\KidputerControl.Common.psm1'
if (-not (Test-Path $module)) { $module = Join-Path $PSScriptRoot 'KidputerControl\Modules\KidputerControl.Common.psm1' }
if (Test-Path $module) {
    Import-Module $module -Force
    $paths = Initialize-KcPaths -DataRoot $dataRoot
    try {
        $cfg = Read-KcJsonFile -Path $paths.AgentFile
        $token = Get-KcDeviceToken
        if ($cfg -and $token) {
            Add-KcEvent -Type 'agent.uninstalled' -Severity warning -Message "Agent uninstalled by $([Environment]::UserDomainName)\$([Environment]::UserName)"
            [void](Send-KcQueuedEvents -BaseUrl ([string]$cfg.api_url) -Token $token)
        }
    } catch { Write-Warning "Could not notify the cloud: $($_.Exception.Message)" }
}

if (Get-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskPath $taskPath -TaskName $taskName -Confirm:$false
    Write-Host 'Removed scheduled task.'
}
# Remove the (now empty) task folder.
try {
    $svc = New-Object -ComObject Schedule.Service
    $svc.Connect()
    $svc.GetFolder('\').DeleteFolder('KidputerControl', 0)
} catch { }

Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
    Where-Object { $_.CommandLine -and ($_.CommandLine.Contains("$installRoot\Watchdog.ps1") -or $_.CommandLine.Contains("$installRoot\UiAgent.ps1")) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

Remove-Item -LiteralPath $installRoot -Recurse -Force -ErrorAction SilentlyContinue
if ($KeepLogs) {
    Get-ChildItem -LiteralPath $dataRoot -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne 'logs' } | Remove-Item -Recurse -Force
    Write-Host "Logs kept in $dataRoot\logs"
} else {
    Remove-Item -LiteralPath $dataRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host 'Kidputer Control has been removed. Delete this device in the dashboard to revoke its token.' -ForegroundColor Green
