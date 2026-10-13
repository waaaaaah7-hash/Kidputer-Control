#Requires -Version 5.1
<#
.SYNOPSIS
    Kidputer Control folder-size scan (roadmap #19). Started by the watchdog as a separate process in
    Windows' background mode, so a scan never slows games down or holds up enforcement.

.DESCRIPTION
    Writes the result (JSON: folder tree with totals, largest files, space by type) to -OutFile, and
    progress to -ProgressFile while it runs. Names, sizes and dates only: no file is ever opened.
#>
param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$OutFile,
    [string]$ProgressFile,
    # Folders smaller than this (below the top two levels) are folded into their parent's totals.
    [long]$MinNodeBytes = 52428800,
    [int]$MaxDepth = 12
)

$ErrorActionPreference = 'Stop'
if (-not ('KcScanner' -as [type])) {
    Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'Modules\KidputerControl.Scanner.cs')))
}
[void][KcScanner]::EnterBackgroundMode()
try { [Diagnostics.Process]::GetCurrentProcess().PriorityClass = [Diagnostics.ProcessPriorityClass]::Idle } catch { }

$json = [KcScanner]::ScanToJson($Root, $ProgressFile, $MinNodeBytes, $MaxDepth)
$tmp = "$OutFile.tmp"
[IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))
if ([IO.File]::Exists($OutFile)) { [IO.File]::Delete($OutFile) }
[IO.File]::Move($tmp, $OutFile)
