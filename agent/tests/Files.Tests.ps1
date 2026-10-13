<#
    Unit tests for KidputerControl.Files.psm1 (drives and the file explorer). Self-contained:
    powershell -File agent\tests\Files.Tests.ps1
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TestHarness.ps1')
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Common.psm1') -Force
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Files.psm1') -Force

function D([string]$s) { [DateTimeOffset]::Parse($s, [Globalization.CultureInfo]::InvariantCulture) }

Test 'only full local folder paths are accepted' {
    Assert-True (Test-KcFolderPath 'C:\Users\Sam\Downloads') 'local'
    Assert-True (Test-KcFolderPath 'D:\') 'a drive'
    foreach ($bad in '\\server\share', 'Users\Sam', 'C:\Users\..\Windows', '', '\\?\C:\x', 'C:') {
        Assert-Equal $false (Test-KcFolderPath $bad)
    }
}

Test 'folder listing: folders first, then files, with sizes only for files' {
    $root = Join-Path ([IO.Path]::GetTempPath()) ("kc-list-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path (Join-Path $root 'zeta') | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $root 'alpha') | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $root 'b.txt'), (New-Object byte[] 10))
    [IO.File]::WriteAllBytes((Join-Path $root 'a.zip'), (New-Object byte[] 20))
    $l = Get-KcFolderListing -Path $root
    Assert-Equal @('alpha', 'zeta', 'a.zip', 'b.txt') @($l.entries | ForEach-Object { $_.n })
    Assert-Equal $true $l.entries[0].dir
    Assert-Equal $false $l.entries[0].Contains('s')
    Assert-Equal 20 $l.entries[2].s
    Assert-Equal 4 $l.total
    Assert-Equal $false $l.truncated
    $small = Get-KcFolderListing -Path $root -Max 2
    Assert-Equal 2 @($small.entries).Count
    Assert-Equal $true $small.truncated
    $threw = $false
    try { [void](Get-KcFolderListing -Path (Join-Path $root 'missing')) } catch { $threw = $true; Assert-Match "doesn't exist" $_.Exception.Message }
    Assert-True $threw 'missing folder'
    Remove-Item -Recurse -Force $root
}

Test 'small folders are folded in: 0.2% of the used space, at least 50 MB' {
    Assert-Equal (50MB) (Get-KcScanMinBytes -UsedBytes 1GB)
    Assert-Equal ([long](500GB * 0.002)) (Get-KcScanMinBytes -UsedBytes 500GB)
}

Test 'regular scans: fixed drives only, the most overdue first' {
    $drives = @(
        @{ name = 'C:\'; type = 'Fixed' }, @{ name = 'D:\'; type = 'Fixed' }, @{ name = 'E:\'; type = 'Removable' }
    )
    $now = D '2026-01-20T12:00:00Z'
    Assert-Equal 'C' (Get-KcDueScan -Drives $drives -LastScans @{} -EveryDays 7 -Now $now)        # never scanned
    $last = @{ C = '2026-01-19T00:00:00Z'; D = '2026-01-01T00:00:00Z' }
    Assert-Equal 'D' (Get-KcDueScan -Drives $drives -LastScans $last -EveryDays 7 -Now $now)
    $recent = @{ C = '2026-01-19T00:00:00Z'; D = '2026-01-18T00:00:00Z' }
    Assert-Equal $null (Get-KcDueScan -Drives $drives -LastScans $recent -EveryDays 7 -Now $now)
    Assert-Equal $null (Get-KcDueScan -Drives $drives -LastScans @{} -EveryDays 0 -Now $now)
    $fromJson = '{"C":"2026-01-19T00:00:00Z"}' | ConvertFrom-Json
    Assert-Equal 'D' (Get-KcDueScan -Drives $drives -LastScans $fromJson -EveryDays 7 -Now $now)
}

Test 'drive report lists ready drives with sizes' {
    $r = Get-KcDriveReport
    Assert-True (@($r.drives).Count -ge 1) 'at least one drive'
    Assert-True (@($r.drives | Where-Object { [long]$_.size -gt 0 }).Count -ge 1) 'a drive with a size'
}

Complete-Tests
