<#
    Unit tests for KidputerControl.Apps.psm1 (running apps and closing them). Self-contained:
    powershell -File agent\tests\Apps.Tests.ps1
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TestHarness.ps1')
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Common.psm1') -Force
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Apps.psm1') -Force

$win = 'C:\Windows'
$install = 'C:\Program Files\KidputerControl'

function P($Id, $Name, $Path, $HasWindow = $true) {
    [pscustomobject]@{ Id = $Id; Name = $Name; Path = $Path; StartTime = '2026-01-12T20:00:00.000Z'; MemoryMB = 100; HasWindow = $HasWindow; Title = $null }
}

$procs = @(
    (P 1 'javaw' 'C:\Program Files\Minecraft\runtime\javaw.exe'),
    (P 2 'javaw' 'c:\program files\minecraft\RUNTIME\javaw.exe'),
    (P 3 'chrome' 'C:\Program Files\Google\Chrome\chrome.exe'),
    (P 4 'explorer' 'C:\Windows\explorer.exe'),
    (P 5 'powershell' 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' $false),
    (P 6 'mystery' $null $false)
)

Test 'closing an app closes every copy of that program, matching paths without case' {
    $t = Select-KcProcessesToStop -Processes $procs -Path 'C:\Program Files\Minecraft\runtime\javaw.exe' -WindowsDir $win -InstallRoot $install
    Assert-Equal @(1, 2) @($t | ForEach-Object { $_.Id })
}

Test 'Windows itself and Kidputer Control can never be closed' {
    Assert-Equal 0 (Select-KcProcessesToStop -Processes $procs -Path 'C:\Windows\explorer.exe' -WindowsDir $win).Count
    Assert-True (Test-KcProtectedProcess -Name 'svchost' -Path 'D:\elsewhere\svchost.exe' -WindowsDir $win) 'protected by name'
    Assert-True (Test-KcProtectedProcess -Name 'x' -Path 'C:\Windows\System32\notepad.exe' -WindowsDir $win) 'anything in the Windows folder'
    Assert-True (Test-KcProtectedProcess -Name 'x' -Path "$install\helper.exe" -WindowsDir $win -InstallRoot $install) 'our own files'
    Assert-True (Test-KcProtectedProcess -Name 'x' -Path $null -WindowsDir $win) 'unknown path'
    Assert-Equal $false (Test-KcProtectedProcess -Name 'chrome' -Path 'C:\Program Files\Google\Chrome\chrome.exe' -WindowsDir $win -InstallRoot $install)
}

Test 'a folder that merely starts with the Windows folder name is not Windows' {
    Assert-Equal $false (Test-KcProtectedProcess -Name 'game' -Path 'C:\WindowsGames\game.exe' -WindowsDir $win)
}

Test 'process report: friendly names, windows, and Windows processes marked as system' {
    $lookup = { param($p) if ($p -like '*javaw.exe') { @{ product = 'Java'; description = 'Java(TM) Platform SE binary'; company = 'Oracle' } } else { @{ product = $null; description = $null; company = $null } } }
    $r = ConvertTo-KcProcessReport -Processes $procs -VersionLookup $lookup -WindowsDir $win
    Assert-Equal 6 $r.Count
    Assert-Equal 'Java(TM) Platform SE binary' $r[0].description
    Assert-Equal 'Oracle' $r[0].company
    Assert-Equal $true $r[0].has_window
    Assert-Equal $false $r[0].system
    Assert-Equal $true $r[3].system     # explorer
    Assert-Equal $true $r[5].system     # no readable path
    # Survives the trip to the server.
    $json = ConvertTo-Json -InputObject @{ processes = $r } -Depth 5 -Compress
    Assert-Match '"pid":1' $json
}

Test 'display name prefers the program description, then its product, then the exe name' {
    Assert-Equal 'Minecraft Launcher' (Get-KcAppDisplayName -Process (P 1 'MinecraftLauncher' 'x') -VersionInfo @{ description = 'Minecraft Launcher'; product = 'Minecraft' })
    Assert-Equal 'Minecraft' (Get-KcAppDisplayName -Process (P 1 'x' 'x') -VersionInfo @{ description = $null; product = 'Minecraft' })
    Assert-Equal 'game' (Get-KcAppDisplayName -Process (P 1 'game' 'x') -VersionInfo $null)
}

Test 'local state keeps handled commands and pending results across restarts' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("kc-apps-" + [guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($tmp)
    $file = Join-Path $tmp 'local.json'
    $l = New-KcLocalState
    $l.handled_commands = Add-KcBoundedItem -List $l.handled_commands -Item 'c1' -Max 200
    $l.command_results = @([pscustomobject]@{ id = 'c1'; status = 'done'; result = @{ closed = 2 } })
    Write-KcJsonFile -Path $file -Object $l
    $back = Read-KcLocalState -Path $file
    Assert-Equal @('c1') @($back.handled_commands)
    Assert-Equal 'done' @($back.command_results)[0].status
    Assert-Equal 2 @($back.command_results)[0].result.closed
    Remove-Item -Recurse -Force $tmp
}

Complete-Tests
