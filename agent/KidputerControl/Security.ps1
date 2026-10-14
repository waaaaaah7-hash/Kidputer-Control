#Requires -Version 5.1
<#
.SYNOPSIS
    Kidputer Control security job (roadmap #23). Started by the watchdog as a separate, low-priority
    process for anything slow: a Defender scan, updating virus definitions, removing threats,
    searching for Windows updates or installing them. One job at a time.

.DESCRIPTION
    Writes the outcome as JSON to -OutFile: action, ok, message, started_at, finished_at, plus
    "updates" (what's waiting) for check_updates and install_updates, and "installed", "failed" and
    "reboot_required" for install_updates.
#>
param(
    [Parameter(Mandatory)][ValidateSet('quick_scan', 'full_scan', 'update_signatures', 'remove_threats', 'check_updates', 'install_updates')]
    [string]$Action,
    [Parameter(Mandatory)][string]$OutFile,
    [switch]$IncludeFeatureUpdates
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Modules\KidputerControl.Common.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'Modules\KidputerControl.Security.psm1') -Force
try { [Diagnostics.Process]::GetCurrentProcess().PriorityClass = [Diagnostics.ProcessPriorityClass]::BelowNormal } catch { }

$result = [ordered]@{ action = $Action; ok = $false; message = $null; started_at = (ConvertTo-KcIso (Get-KcNow)) }
try {
    switch ($Action) {
        'quick_scan' { Start-MpScan -ScanType QuickScan }
        'full_scan' { Start-MpScan -ScanType FullScan }
        'update_signatures' { Update-MpSignature }
        'remove_threats' { Remove-MpThreat }
        'check_updates' { $result.updates = @((Find-KcWindowsUpdates).List) }
        'install_updates' {
            $inst = Install-KcWindowsUpdates -IncludeFeature ([bool]$IncludeFeatureUpdates)
            foreach ($k in $inst.Keys) { $result[$k] = $inst[$k] }
        }
    }
    $result.ok = $true
} catch {
    $result.message = $_.Exception.Message
}
$result.finished_at = ConvertTo-KcIso (Get-KcNow)
Write-KcJsonFile -Path $OutFile -Object $result
