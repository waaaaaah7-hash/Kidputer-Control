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

# --- App rules and usage ------------------------------------------------------------------------------------

function D([string]$s) { [DateTimeOffset]::Parse($s, [Globalization.CultureInfo]::InvariantCulture) }
$catalog = ('[
  {"id":"minecraft_java","name":"Minecraft (Java)","category":"games","matchers":[{"exe":"javaw.exe","cmdline":"net.minecraft"},{"exe":"MinecraftLauncher.exe"}]},
  {"id":"steam_games","name":"Steam games","category":"games","matchers":[{"path":"\\steamapps\\common\\"}]},
  {"id":"roblox","name":"Roblox","category":"games","matchers":[{"exe":"RobloxPlayerBeta.exe"}]},
  {"id":"chrome","name":"Google Chrome","category":"browsers","matchers":[{"exe":"chrome.exe"}]},
  {"id":"custom:1","name":"My Steam Game","category":"Homework apps","matchers":[{"exe":"special.exe"}]}
]' | ConvertFrom-Json)

function Id($Path, $Original = $null, $Cmd = $null) { New-KcAppIdentity -Path $Path -VersionInfo @{ original_name = $Original } -CommandLine $Cmd }

Test 'apps are recognised by file name, renamed copies by their original name, Minecraft Java by its command line' {
    Assert-Equal 'roblox' (Find-KcCatalogApp -Identity (Id 'C:\Roblox\RobloxPlayerBeta.exe') -Catalog $catalog).id
    Assert-Equal 'roblox' (Find-KcCatalogApp -Identity (Id 'C:\Users\Sam\homework.exe' 'RobloxPlayerBeta.exe') -Catalog $catalog).id
    Assert-Equal 'minecraft_java' (Find-KcCatalogApp -Identity (Id 'C:\Java\bin\javaw.exe' $null '-cp x net.minecraft.client.main.Main') -Catalog $catalog).id
    Assert-Equal $null (Find-KcCatalogApp -Identity (Id 'C:\Java\bin\javaw.exe' $null '-jar tool.jar') -Catalog $catalog)
    Assert-Equal 'steam_games' (Find-KcCatalogApp -Identity (Id 'D:\SteamLibrary\steamapps\common\Terraria\Terraria.exe') -Catalog $catalog).id
    Assert-Equal $null (Find-KcCatalogApp -Identity (Id 'C:\Tools\notes.exe') -Catalog $catalog)
}

Test "the parents' own apps are checked before the catalogue" {
    $id = Id 'D:\SteamLibrary\steamapps\common\Thing\special.exe'
    Assert-Equal 'custom:1' (Find-KcCatalogApp -Identity $id -Catalog $catalog).id
}

$rules = ('[
  {"id":"r1","target_kind":"app","target":"minecraft_java","mode":"allow","windows":[],"allowed":[]},
  {"id":"r2","target_kind":"app","target":"roblox","mode":"block","windows":[],"allowed":[]},
  {"id":"r3","target_kind":"category","target":"games","mode":"schedule",
   "windows":[{"day":0,"start":"16:00","end":"19:00"}],
   "allowed":[{"start":"2026-01-12T16:00:00Z","end":"2026-01-12T19:00:00Z"},{"start":"2026-01-13T16:00:00Z","end":"2026-01-13T19:00:00Z"}]}
]' | ConvertFrom-Json)

Test "an app's own rule beats its category's rule" {
    $mc = $catalog | Where-Object { $_.id -eq 'minecraft_java' }
    $steam = $catalog | Where-Object { $_.id -eq 'steam_games' }
    $chrome = $catalog | Where-Object { $_.id -eq 'chrome' }
    Assert-Equal 'r1' (Get-KcAppRule -App $mc -Rules $rules).id
    Assert-Equal 'r3' (Get-KcAppRule -App $steam -Rules $rules).id
    Assert-Equal $null (Get-KcAppRule -App $chrome -Rules $rules)
    Assert-Equal $null (Get-KcAppRule -App $null -Rules $rules)
}

Test 'allowed, blocked, and scheduled apps; outside the window the next start is known' {
    $now = D '2026-01-12T20:00:00Z'
    Assert-Equal $true (Test-KcAppAllowed -Rule $rules[0] -Now $now).Allowed
    Assert-Equal $false (Test-KcAppAllowed -Rule $rules[1] -Now $now).Allowed
    Assert-Equal $true (Test-KcAppAllowed -Rule $null -Now $now).Allowed
    $in = Test-KcAppAllowed -Rule $rules[2] -Now (D '2026-01-12T17:00:00Z')
    Assert-Equal $true $in.Allowed
    Assert-Equal (D '2026-01-12T19:00:00Z') $in.Until
    $out = Test-KcAppAllowed -Rule $rules[2] -Now $now
    Assert-Equal $false $out.Allowed
    Assert-Equal (D '2026-01-13T16:00:00Z') $out.NextStart
    Assert-Match "available from" (Get-KcBlockedMessage -Name 'Steam games' -Decision $out)
    Assert-Equal "Roblox isn't allowed on this PC." (Get-KcBlockedMessage -Name 'Roblox' -Decision (Test-KcAppAllowed -Rule $rules[1] -Now $now))
}

Test 'offline for weeks: schedules fall back to the weekly times on the PC clock' {
    $utc = [TimeZoneInfo]::Utc
    Assert-Equal $true (Test-KcAppAllowed -Rule $rules[2] -Now (D '2026-03-02T17:30:00Z') -UseWeekly -TimeZone $utc).Allowed   # a Monday
    Assert-Equal $false (Test-KcAppAllowed -Rule $rules[2] -Now (D '2026-03-03T17:30:00Z') -UseWeekly -TimeZone $utc).Allowed  # Tuesday
}

Test 'usage is counted per local day from the server day boundaries' {
    $days = @([pscustomobject]@{ day = '2026-01-12'; start = '2026-01-12T00:00:00Z' }, [pscustomobject]@{ day = '2026-01-13'; start = '2026-01-13T00:00:00Z' })
    Assert-Equal '2026-01-12' (Get-KcUsageDay -Now (D '2026-01-12T23:59:00Z') -DayStarts $days)
    Assert-Equal '2026-01-13' (Get-KcUsageDay -Now (D '2026-01-13T00:00:00Z') -DayStarts $days)
    Assert-Equal $null (Get-KcUsageDay -Now (D '2026-01-11T12:00:00Z') -DayStarts $days)
    $u = @{}
    Add-KcUsage -Usage $u -Day '2026-01-12' -Key 'roblox' -Name 'Roblox' -Category 'games' -Seconds 60
    Add-KcUsage -Usage $u -Day '2026-01-12' -Key 'roblox' -Name 'Roblox' -Category 'games' -Seconds 30
    Assert-Equal 90 $u['2026-01-12']['roblox'].seconds
    for ($i = 1; $i -le 10; $i++) { Add-KcUsage -Usage $u -Day ('2026-02-{0:00}' -f $i) -Key '_total' -Name 'Screen time' -Seconds 1 }
    Assert-Equal 8 $u.Count
    Assert-Equal $false $u.ContainsKey('2026-01-12')
}

Complete-Tests
