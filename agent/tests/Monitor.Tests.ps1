<#
    Unit tests for KidputerControl.Monitor.psm1 (change monitoring). Self-contained:
    powershell -File agent\tests\Monitor.Tests.ps1
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TestHarness.ps1')
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Common.psm1') -Force
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Monitor.psm1') -Force

function Reg($Name, $Publisher = 'Pub', $Extra = @{}) {
    $h = @{ DisplayName = $Name; Publisher = $Publisher; DisplayVersion = '1.0' }
    foreach ($k in $Extra.Keys) { $h[$k] = $Extra[$k] }
    [pscustomobject]$h
}

Test 'installed programs: Windows components, updates and duplicates are left out' {
    $entries = @(
        (Reg 'Steam' 'Valve'),
        (Reg 'Steam' 'Valve'),
        (Reg '  '),
        (Reg 'KB5034441 Security Update' 'Microsoft' @{ ReleaseType = 'Security Update' }),
        (Reg 'Some Runtime' 'X' @{ SystemComponent = 1 }),
        (Reg 'Office Language Pack' 'Microsoft' @{ ParentKeyName = 'Office16' }),
        (Reg 'Discord' 'Discord Inc.')
    )
    $p = Select-KcInstalledPrograms -Entries $entries -Scope 'user'
    Assert-Equal @('Discord', 'Steam') @($p | ForEach-Object { $_.name })
    Assert-Equal 'user' $p[0].scope
}

Test 'program changes become install and uninstall events; VPNs get their own event' {
    $old = Select-KcInstalledPrograms -Entries @((Reg 'Steam' 'Valve'), (Reg 'Paint.NET' 'dotPDN'))
    $new = Select-KcInstalledPrograms -Entries @((Reg 'Steam' 'Valve'), (Reg 'NordVPN' 'Nord Security'), (Reg 'Roblox Player' 'Roblox Corporation'))
    $ev = Get-KcProgramChangeEvents -Old $old -New $new
    Assert-Equal @('software.installed', 'software.vpn_detected', 'software.installed', 'software.uninstalled') @($ev | ForEach-Object { $_.type })
    Assert-Equal 'Installed: NordVPN (Nord Security)' $ev[0].message
    Assert-Equal 'vpn' $ev[1].data.kind
    Assert-Equal 'Removed: Paint.NET' $ev[3].message
    Assert-Equal 0 (Get-KcProgramChangeEvents -Old $new -New $new).Count
}

Test 'VPN and remote-control apps are recognised by name or publisher' {
    Assert-Equal 'vpn' (Find-KcFlaggedApp -Names @('ProtonVPN'))
    Assert-Equal 'vpn' (Find-KcFlaggedApp -Names @('Something', 'Private Internet Access, Inc.'))
    Assert-Equal 'remote_access' (Find-KcFlaggedApp -Names @('AnyDesk'))
    Assert-Equal 'remote_access' (Find-KcFlaggedApp -Names @('TeamViewer 15'))
    Assert-Equal $null (Find-KcFlaggedApp -Names @('Minecraft', $null, ''))
}

Test 'program identity survives version-numbered folders' {
    Assert-Equal (ConvertTo-KcProgramKey 'C:\Program Files\Google\Chrome\Application\131.0.6778.86\chrome.exe') (ConvertTo-KcProgramKey 'C:\Program Files\Google\Chrome\Application\132.0.1.2\chrome.exe')
    Assert-Equal (ConvertTo-KcProgramKey 'C:\Users\Sam\AppData\Local\Discord\app-1.0.9163\Discord.exe') (ConvertTo-KcProgramKey 'C:\Users\Sam\AppData\Local\Discord\app-1.0.9170\Discord.exe')
    Assert-True ((ConvertTo-KcProgramKey 'C:\Games\a\game.exe') -ne (ConvertTo-KcProgramKey 'C:\Games\b\game.exe')) 'different folders stay different'
}

Test 'where a program was run from: straight from an archive, USB, Downloads' {
    Assert-Equal 'archive' (Get-KcRunOrigin 'C:\Users\Sam\AppData\Local\Temp\Temp1_game.zip\game\game.exe')
    Assert-Equal 'archive' (Get-KcRunOrigin 'C:\Users\Sam\AppData\Local\Temp\Rar$EXa1234.5678\setup.exe')
    Assert-Equal 'archive' (Get-KcRunOrigin 'C:\Users\Sam\AppData\Local\Temp\7zO8F2A1B3C\game.exe')
    Assert-Equal 'usb' (Get-KcRunOrigin 'E:\Games\game.exe' -RemovableDrives @('E:\'))
    Assert-Equal 'downloads' (Get-KcRunOrigin 'C:\Users\Sam\Downloads\tool.exe')
    Assert-Equal 'temp' (Get-KcRunOrigin 'C:\Users\Sam\AppData\Local\Temp\setup123\x.exe')
    Assert-Equal $null (Get-KcRunOrigin 'C:\Program Files\Steam\steam.exe')
}

Test 'first run: reported once per program, quietly while learning, never for Windows' {
    $seen = @{}
    $p = [pscustomobject]@{ Name = 'game'; Path = 'C:\Users\Sam\AppData\Local\Temp\Temp1_game.zip\game.exe' }
    $ev = Get-KcFirstRunEvent -Process $p -Seen $seen -Now '2026-01-01T00:00:00Z' -WindowsDir 'C:\Windows' -VersionInfo @{ description = 'Cool Game'; product = 'Cool Game'; company = 'Indie' }
    Assert-Equal 'program.first_run' $ev.type
    Assert-Equal 'warning' $ev.severity
    Assert-Equal 'First run of Cool Game straight from a zip or other archive' $ev.message
    Assert-Equal 'archive' $ev.data.origin
    Assert-Equal $null (Get-KcFirstRunEvent -Process $p -Seen $seen -Now 'x' -WindowsDir 'C:\Windows')
    $win = [pscustomobject]@{ Name = 'notepad'; Path = 'C:\Windows\System32\notepad.exe' }
    Assert-Equal $null (Get-KcFirstRunEvent -Process $win -Seen $seen -Now 'x' -WindowsDir 'C:\Windows')
    $learned = [pscustomobject]@{ Name = 'steam'; Path = 'C:\Program Files\Steam\steam.exe' }
    Assert-Equal $null (Get-KcFirstRunEvent -Process $learned -Seen $seen -Now 'x' -WindowsDir 'C:\Windows' -Learning)
    Assert-Equal $null (Get-KcFirstRunEvent -Process $learned -Seen $seen -Now 'x' -WindowsDir 'C:\Windows')   # already known
    $vpn = [pscustomobject]@{ Name = 'AnyDesk'; Path = 'C:\Users\Sam\Downloads\AnyDesk.exe' }
    Assert-Equal 'remote_access' (Get-KcFirstRunEvent -Process $vpn -Seen $seen -Now 'x' -WindowsDir 'C:\Windows').data.flagged
}

Test 'first-run memory stays bounded, forgetting the oldest' {
    $seen = @{ a = '2026-01-01'; b = '2026-01-03'; c = '2026-01-02' }
    Limit-KcSeenPrograms -Seen $seen -Max 2
    Assert-Equal @('b', 'c') @($seen.Keys | Sort-Object)
}

Test 'account changes: new accounts and new administrators are warnings' {
    $old = @{ users = @([pscustomobject]@{ name = 'Parent'; sid = 'S-1' }, [pscustomobject]@{ name = 'Sam'; sid = 'S-2' }); admins = @('S-1') }
    $new = @{ users = @([pscustomobject]@{ name = 'Parent'; sid = 'S-1' }, [pscustomobject]@{ name = 'Sam'; sid = 'S-2' }, [pscustomobject]@{ name = 'Gamer'; sid = 'S-3' }); admins = @('S-1', 'S-2') }
    $ev = Get-KcAccountChangeEvents -Old $old -New $new
    Assert-Equal @('account.created', 'account.admin_added') @($ev | ForEach-Object { $_.type })
    Assert-Equal 'Sam is now an administrator' $ev[1].message
    $back = Get-KcAccountChangeEvents -Old $new -New $old
    Assert-Equal @('account.deleted', 'account.admin_removed') @($back | ForEach-Object { $_.type })
}

Test 'archive names are recognised by extension' {
    Assert-True (Test-KcArchiveName 'C:\Users\Sam\Downloads\Game.ZIP') 'zip'
    Assert-True (Test-KcArchiveName 'mods.7z') '7z'
    Assert-Equal $false (Test-KcArchiveName 'notes.txt')
}

Test 'USB summary counts programs and archives near the top of the drive' {
    $root = Join-Path ([IO.Path]::GetTempPath()) ("kc-usb-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path (Join-Path $root 'Games') | Out-Null
    foreach ($f in 'setup.exe', 'pics.zip', 'readme.txt', 'Games/game.exe', 'Games/mods.7z') { [IO.File]::WriteAllText((Join-Path $root $f), 'x') }
    $s = Get-KcDriveSummary -Root $root
    Assert-Equal 2 $s.programs
    Assert-Equal 2 $s.archives
    Assert-Equal $false $s.partial
    Remove-Item -Recurse -Force $root
}

Complete-Tests
