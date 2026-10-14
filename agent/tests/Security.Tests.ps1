<#
    Unit tests for KidputerControl.Security.psm1 (the Security tab: Defender and Windows Update).
    Self-contained: powershell -File agent\tests\Security.Tests.ps1
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TestHarness.ps1')
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Common.psm1') -Force
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Security.psm1') -Force

function D([string]$s) { [DateTimeOffset]::Parse($s, [Globalization.CultureInfo]::InvariantCulture) }

Test 'threat history: names, severities, plain file paths, newest first' {
    $threats = @(
        [pscustomobject]@{ ThreatID = 2147735505; ThreatName = 'Trojan:Win32/Wacatac.B!ml'; SeverityID = 5 },
        [pscustomobject]@{ ThreatID = 311978; ThreatName = 'PUA:Win32/Presenoker'; SeverityID = 1 }
    )
    $detections = @(
        [pscustomobject]@{ DetectionID = '{A}'; ThreatID = 311978; ThreatStatusID = 3; InitialDetectionTime = [datetime]::new(2026, 1, 10, 9, 0, 0, [DateTimeKind]::Utc)
            Resources = @('file:_C:\Users\Sam\Downloads\setup.exe') },
        [pscustomobject]@{ DetectionID = '{B}'; ThreatID = 2147735505; ThreatStatusID = 103; InitialDetectionTime = [datetime]::new(2026, 1, 12, 20, 30, 0, [DateTimeKind]::Utc)
            Resources = @('containerfile:_C:\Users\Sam\Downloads\mods.zip', 'file:_C:\Users\Sam\Downloads\mods.zip->cheat.exe') }
    )
    $list = ConvertTo-KcThreatList -Detections $detections -Threats $threats
    Assert-Equal 2 $list.Count
    Assert-Equal '{B}' $list[0].id
    Assert-Equal 'Trojan:Win32/Wacatac.B!ml' $list[0].name
    Assert-Equal 'severe' $list[0].severity
    Assert-Equal 'remove_failed' $list[0].status
    Assert-Equal $true $list[0].needs_action
    Assert-Equal 'C:\Users\Sam\Downloads\mods.zip' $list[0].files[0]
    Assert-Equal '2026-01-12T20:30:00.000Z' $list[0].detected_at
    Assert-Equal 'quarantined' $list[1].status
    Assert-Equal $false $list[1].needs_action
    Assert-Equal 'low' $list[1].severity
}

Test 'threat history: unknown threats and codes still read sensibly' {
    $list = ConvertTo-KcThreatList -Detections @([pscustomobject]@{ DetectionID = '{C}'; ThreatID = 42; ThreatStatusID = 999 }) -Threats @()
    Assert-Equal 'Threat 42' $list[0].name
    Assert-Equal 'unknown' $list[0].status
    Assert-Equal $true $list[0].needs_action
    Assert-Equal $null $list[0].detected_at
    Assert-Equal 0 (ConvertTo-KcThreatList -Detections @() -Threats @()).Count
}

Test 'Security Center product state: on or off, up to date or not' {
    $on = ConvertFrom-KcProductState 397568     # 0x061100
    Assert-Equal $true $on.enabled
    Assert-Equal $true $on.up_to_date
    $off = ConvertFrom-KcProductState 393472    # 0x060100
    Assert-Equal $false $off.enabled
    $stale = ConvertFrom-KcProductState 397584  # 0x061110
    Assert-Equal $true $stale.enabled
    Assert-Equal $false $stale.up_to_date
}

Test 'never-run scans read as no date' {
    Assert-Equal $null (Get-KcScanAgeDate ([datetime]::new(1601, 1, 1)))
    Assert-Equal $null (Get-KcScanAgeDate $null)
    Assert-Equal '2026-01-12T20:30:00.000Z' (Get-KcScanAgeDate ([datetime]::new(2026, 1, 12, 20, 30, 0, [DateTimeKind]::Utc)))
}

Test 'update kinds, and which ones get installed' {
    Assert-Equal 'security' (Get-KcUpdateKind -Categories @('Security Updates', 'Windows 11') -Title '2026-01 Cumulative Update')
    Assert-Equal 'feature' (Get-KcUpdateKind -Categories @('Upgrades') -Title 'Windows 11, version 25H2')
    Assert-Equal 'feature' (Get-KcUpdateKind -Categories @('Windows 11') -Title 'Feature update to Windows 11, version 25H2')
    Assert-Equal 'definition' (Get-KcUpdateKind -Categories @('Definition Updates') -Title 'Security Intelligence Update')
    Assert-Equal 'driver' (Get-KcUpdateKind -Categories @('Drivers') -Title 'Realtek - Audio')
    Assert-Equal 'quality' (Get-KcUpdateKind -Categories @('Updates') -Title '.NET Framework')
    Assert-Equal $true (Test-KcUpdateWanted -Kind 'security' -IncludeFeature $false)
    Assert-Equal $false (Test-KcUpdateWanted -Kind 'feature' -IncludeFeature $false)
    Assert-Equal $true (Test-KcUpdateWanted -Kind 'feature' -IncludeFeature $true)
    Assert-Equal $false (Test-KcUpdateWanted -Kind 'driver' -IncludeFeature $true)
}

Test 'installs wait for mains power or a battery above half' {
    Assert-Equal $true (Test-KcPowerOk -Batteries @())
    Assert-Equal $true (Test-KcPowerOk -Batteries @([pscustomobject]@{ BatteryStatus = 2; EstimatedChargeRemaining = 10 }))   # charging
    Assert-Equal $true (Test-KcPowerOk -Batteries @([pscustomobject]@{ BatteryStatus = 1; EstimatedChargeRemaining = 80 }))
    Assert-Equal $false (Test-KcPowerOk -Batteries @([pscustomobject]@{ BatteryStatus = 1; EstimatedChargeRemaining = 30 }))
}

Test 'restart for updates: only after 20 minutes of bedtime lock, once a night, when allowed' {
    $now = D '2026-01-12T21:30:00Z'
    Assert-Equal $true (Test-KcRestartDue -Allowed $true -RebootRequired $true -LockedSince (D '2026-01-12T21:00:00Z') -LastRestartAt $null -Now $now)
    Assert-Equal $false (Test-KcRestartDue -Allowed $true -RebootRequired $true -LockedSince (D '2026-01-12T21:15:00Z') -LastRestartAt $null -Now $now)
    Assert-Equal $false (Test-KcRestartDue -Allowed $true -RebootRequired $true -LockedSince $null -LastRestartAt $null -Now $now)
    Assert-Equal $false (Test-KcRestartDue -Allowed $false -RebootRequired $true -LockedSince (D '2026-01-12T21:00:00Z') -LastRestartAt $null -Now $now)
    Assert-Equal $false (Test-KcRestartDue -Allowed $true -RebootRequired $false -LockedSince (D '2026-01-12T21:00:00Z') -LastRestartAt $null -Now $now)
    Assert-Equal $false (Test-KcRestartDue -Allowed $true -RebootRequired $true -LockedSince (D '2026-01-12T21:00:00Z') -LastRestartAt '2026-01-12T20:00:00Z' -Now $now)
    Assert-Equal $true (Test-KcRestartDue -Allowed $true -RebootRequired $true -LockedSince (D '2026-01-12T21:00:00Z') -LastRestartAt '2026-01-11T21:20:00Z' -Now $now)
}

Test 'readings never throw, even off Windows' {
    $def = Get-KcDefenderReport
    Assert-True ($def.Contains('available')) 'defender report'
    $upd = Get-KcUpdateInfo
    Assert-True ($upd.Contains('reboot_required')) 'update info'
    Assert-True ($null -ne $upd.windows) 'windows version'
}

Test 'metered connections wait; Home editions ignore update policies' {
    Assert-Equal $false (Test-KcCostMetered -NetworkCostType 'Unrestricted' -Roaming $false -OverDataLimit $false)
    Assert-Equal $true (Test-KcCostMetered -NetworkCostType 'Fixed' -Roaming $false -OverDataLimit $false)
    Assert-Equal $true (Test-KcCostMetered -NetworkCostType 'Variable' -Roaming $false -OverDataLimit $false)
    Assert-Equal $true (Test-KcCostMetered -NetworkCostType 'Unrestricted' -Roaming $true -OverDataLimit $false)
    Assert-Equal $false (Test-KcMeteredConnection)   # can't tell here: never blocks
    Assert-Equal $false (Test-KcEditionHonoursPolicies 'Core')
    Assert-Equal $false (Test-KcEditionHonoursPolicies 'CoreSingleLanguage')
    Assert-Equal $true (Test-KcEditionHonoursPolicies 'Professional')
    Assert-Equal $true (Test-KcEditionHonoursPolicies 'Education')
    Assert-Equal $false (Test-KcEditionHonoursPolicies '')
}

Test 'days since the last virus scan, quick or full' {
    $now = D '2026-01-20T12:00:00Z'
    Assert-Equal 2 ([int](Get-KcLastScanAge -QuickScanAt '2026-01-18T12:00:00Z' -FullScanAt '2026-01-01T12:00:00Z' -Now $now))
    Assert-Equal 19 ([int](Get-KcLastScanAge -QuickScanAt $null -FullScanAt '2026-01-01T12:00:00Z' -Now $now))
    Assert-Equal $null (Get-KcLastScanAge -QuickScanAt $null -FullScanAt $null -Now $now)
}

Complete-Tests
