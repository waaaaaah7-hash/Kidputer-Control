#Requires -Version 5.1
<#
    Security tab (roadmap #23): Microsoft Defender (status, threats, scans) and Windows Update
    (what's waiting, installing, restarts).

    The quick readings (Get-KcDefenderReport, Get-KcUpdateInfo) run inside the watchdog. Anything slow
    (a scan, a search for updates, an install) runs in Security.ps1, a separate low-priority process,
    so enforcement never waits for it. The pure functions take plain objects so the tests run
    anywhere. Windows PowerShell 5.1 compatible, plain ASCII.
#>

Set-StrictMode -Version 3.0

# Defender's codes (MSFT_MpThreatDetection.ThreatStatusID and MSFT_MpThreat.SeverityID).
$script:ThreatStatus = @{
    0 = 'unknown'; 1 = 'detected'; 2 = 'cleaned'; 3 = 'quarantined'; 4 = 'removed'; 5 = 'allowed'; 6 = 'blocked'
    102 = 'quarantine_failed'; 103 = 'remove_failed'; 104 = 'allow_failed'; 105 = 'abandoned'; 107 = 'block_failed'
}
$script:ThreatSeverity = @{ 0 = 'unknown'; 1 = 'low'; 2 = 'moderate'; 4 = 'high'; 5 = 'severe' }
# A threat in one of these states is still on the PC and needs someone to act.
$script:NeedsAction = @('detected', 'quarantine_failed', 'remove_failed', 'abandoned', 'block_failed', 'unknown')

function Get-KcThreatStatusName {
    param([int]$Code)
    if ($script:ThreatStatus.ContainsKey($Code)) { return $script:ThreatStatus[$Code] }
    'unknown'
}

function Test-KcThreatNeedsAction {
    param([string]$Status)
    $script:NeedsAction -contains $Status
}

function ConvertTo-KcThreatList {
    <#
        Defender's detection history as plain entries, newest first: id, name, severity, status,
        needs_action, detected_at, files (at most 5 paths). $Detections are MSFT_MpThreatDetection
        objects (or look-alikes), $Threats MSFT_MpThreat ones, for the names and severities.
    #>
    param([object[]]$Detections, [object[]]$Threats, [int]$Max = 50)
    $info = @{}
    foreach ($t in @($Threats)) {
        if ($null -eq $t) { continue }
        $sev = [int](Get-KcProp $t 'SeverityID' 0)
        $name = [string](Get-KcProp $t 'ThreatName' '')
        $sevName = 'unknown'
        if ($script:ThreatSeverity.ContainsKey($sev)) { $sevName = $script:ThreatSeverity[$sev] }
        $info[[string](Get-KcProp $t 'ThreatID' '')] = @{ name = $name; severity = $sevName }
    }
    $list = New-Object System.Collections.ArrayList
    foreach ($d in @($Detections)) {
        if ($null -eq $d) { continue }
        $tid = [string](Get-KcProp $d 'ThreatID' '')
        $status = Get-KcThreatStatusName ([int](Get-KcProp $d 'ThreatStatusID' 0))
        $files = @()
        foreach ($r in @(Get-KcProp $d 'Resources' @())) {
            $text = [string]$r
            # Resources look like "file:_C:\Users\Sam\Downloads\x.exe" or "containerfile:_C:\...\a.zip".
            if ($text -match '^[A-Za-z]+:_(.+)$') { $text = $Matches[1] }
            if ($text.Length -gt 400) { $text = $text.Substring(0, 400) }
            if ($text) { $files += $text }
            if ($files.Count -ge 5) { break }
        }
        $when = ConvertTo-KcDate (Get-KcProp $d 'InitialDetectionTime')
        $name = "Threat $tid"
        $sev = 'unknown'
        if ($info.ContainsKey($tid)) { if ($info[$tid].name) { $name = $info[$tid].name }; $sev = $info[$tid].severity }
        [void]$list.Add([ordered]@{
                id           = [string](Get-KcProp $d 'DetectionID' $tid)
                name         = $name
                severity     = $sev
                status       = $status
                needs_action = (Test-KcThreatNeedsAction $status)
                detected_at  = $(if ($when) { ConvertTo-KcIso $when } else { $null })
                files        = $files
            })
    }
    $sorted = @($list | Sort-Object -Property @{ Expression = { [string]$_.detected_at } } -Descending)
    , @($sorted | Select-Object -First $Max)
}

function ConvertFrom-KcProductState {
    <# Windows Security Center's productState number: is the antivirus on, and are its definitions up to date? #>
    param([long]$State)
    @{
        enabled    = ((($State -shr 8) -band 0x10) -ne 0)
        up_to_date = (($State -band 0xFF) -eq 0)
    }
}

function Get-KcScanAgeDate {
    <# Defender's "last scan" time, or $null when it has never run (it reports year 1 or 1601 then). #>
    param($Value)
    $d = ConvertTo-KcDate $Value
    if ($null -eq $d -or $d.Year -lt 2000) { return $null }
    ConvertTo-KcIso $d
}

function Get-KcDefenderReport {
    <# Defender's state, its threat history, and any other antivirus Windows knows about. Never throws. #>
    $r = [ordered]@{
        available          = $false
        antivirus_enabled  = $false
        realtime           = $false
        tamper_protected   = $null
        running_mode       = $null
        signatures_updated = $null
        signature_version  = $null
        quick_scan_at      = $null
        full_scan_at       = $null
        threats            = @()
        other_antivirus    = @()
    }
    try {
        $s = Get-MpComputerStatus -ErrorAction Stop
        $r.available = $true
        $r.antivirus_enabled = [bool](Get-KcProp $s 'AntivirusEnabled' $false)
        $r.realtime = [bool](Get-KcProp $s 'RealTimeProtectionEnabled' $false)
        $tp = Get-KcProp $s 'IsTamperProtected'
        if ($null -ne $tp) { $r.tamper_protected = [bool]$tp }
        $r.running_mode = [string](Get-KcProp $s 'AMRunningMode' '')
        $r.signatures_updated = Get-KcScanAgeDate (Get-KcProp $s 'AntivirusSignatureLastUpdated')
        $r.signature_version = [string](Get-KcProp $s 'AntivirusSignatureVersion' '')
        $r.quick_scan_at = Get-KcScanAgeDate (Get-KcProp $s 'QuickScanEndTime')
        $r.full_scan_at = Get-KcScanAgeDate (Get-KcProp $s 'FullScanEndTime')
    } catch { }
    if ($r.available) {
        try {
            $r.threats = ConvertTo-KcThreatList -Detections @(Get-MpThreatDetection -ErrorAction Stop) -Threats @(Get-MpThreat -ErrorAction Stop)
        } catch { }
    }
    try {
        $others = @(Get-CimInstance -Namespace 'root/SecurityCenter2' -ClassName AntiVirusProduct -ErrorAction Stop)
        $r.other_antivirus = @($others | Where-Object { [string]$_.displayName -notmatch 'Defender' } | ForEach-Object {
                $ps = ConvertFrom-KcProductState ([long]$_.productState)
                [ordered]@{ name = [string]$_.displayName; enabled = $ps.enabled; up_to_date = $ps.up_to_date }
            })
    } catch { }
    $r
}

# --- Windows Update ------------------------------------------------------------------------------------

function Get-KcUpdateKind {
    <# security, quality (monthly and other updates), definition, feature (a new Windows version) or driver. #>
    param([string[]]$Categories, [string]$Title)
    $cats = @($Categories | ForEach-Object { [string]$_ })
    if ($cats -contains 'Drivers') { return 'driver' }
    if ($cats -contains 'Upgrades' -or $Title -match '(?i)feature update') { return 'feature' }
    if ($cats -contains 'Definition Updates') { return 'definition' }
    if ($cats -contains 'Security Updates' -or $cats -contains 'Critical Updates') { return 'security' }
    'quality'
}

function Test-KcUpdateWanted {
    <# Which updates Kidputer Control installs: never drivers (Windows handles those), feature upgrades only if the parent ticked it. #>
    param([string]$Kind, [bool]$IncludeFeature)
    switch ($Kind) {
        'driver' { return $false }
        'feature' { return $IncludeFeature }
        default { return $true }
    }
}

function Test-KcPowerOk {
    <# Fine to install updates: on mains power, or a battery above 50%. $Batteries: Win32_Battery-like objects. #>
    param([object[]]$Batteries)
    foreach ($b in @($Batteries)) {
        if ($null -eq $b) { continue }
        # BatteryStatus 1 = discharging (on battery).
        if ([int](Get-KcProp $b 'BatteryStatus' 2) -eq 1 -and [int](Get-KcProp $b 'EstimatedChargeRemaining' 100) -lt 50) { return $false }
    }
    $true
}

function Test-KcCostMetered {
    <# Is a connection metered (mobile data, a phone hotspot marked as metered, roaming, or over its data limit)? #>
    param([string]$NetworkCostType, [bool]$Roaming, [bool]$OverDataLimit)
    ($NetworkCostType -eq 'Fixed' -or $NetworkCostType -eq 'Variable' -or $Roaming -or $OverDataLimit)
}

function Test-KcMeteredConnection {
    <# The PC's internet connection is metered, so big downloads should wait. $false when it can't tell. #>
    try {
        $info = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
        $conn = $info::GetInternetConnectionProfile()
        if ($null -eq $conn) { return $false }
        $cost = $conn.GetConnectionCost()
        return (Test-KcCostMetered -NetworkCostType ([string]$cost.NetworkCostType) -Roaming ([bool]$cost.Roaming) -OverDataLimit ([bool]$cost.OverDataLimit))
    } catch { return $false }
}

function Test-KcEditionHonoursPolicies {
    <# Windows Home ignores most Windows Update policies (such as hiding "Pause updates"); Pro, Education and Enterprise follow them. #>
    param([string]$EditionId)
    if (-not $EditionId) { return $false }
    -not ($EditionId -like 'Core*')
}

$script:PauseKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'

function Set-KcPauseBlocked {
    <# Hide (or show again) the "Pause updates" button in Windows Settings, through the SetDisablePauseUXAccess policy. #>
    param([bool]$Block)
    if ($Block) {
        if (-not (Test-Path $script:PauseKey)) { [void](New-Item -Path $script:PauseKey -Force) }
        Set-ItemProperty -Path $script:PauseKey -Name 'SetDisablePauseUXAccess' -Value 1 -Type DWord
    } else {
        Remove-ItemProperty -Path $script:PauseKey -Name 'SetDisablePauseUXAccess' -ErrorAction SilentlyContinue
    }
}

function Test-KcPauseBlocked {
    try { return ([int](Get-ItemProperty -Path $script:PauseKey -Name 'SetDisablePauseUXAccess' -ErrorAction Stop).SetDisablePauseUXAccess -eq 1) } catch { return $false }
}

function Get-KcLastScanAge {
    <# Days since the last quick or full scan, whichever is newer; $null if neither has run. #>
    param($QuickScanAt, $FullScanAt, [Parameter(Mandatory)][DateTimeOffset]$Now)
    $last = $null
    foreach ($v in $QuickScanAt, $FullScanAt) {
        $d = ConvertTo-KcDate $v
        if ($null -ne $d -and ($null -eq $last -or $d -gt $last)) { $last = $d }
    }
    if ($null -eq $last) { return $null }
    ($Now - $last).TotalDays
}

function Test-KcRestartDue {
    <#
        Restart to finish updates now? Only when the parent allows it, a restart is needed, the bedtime
        lock has been on for $MinutesLocked minutes (so the child isn't using the PC), and the PC
        hasn't been asked to restart in the last 12 hours.
    #>
    param(
        [bool]$Allowed,
        [bool]$RebootRequired,
        $LockedSince,
        $LastRestartAt,
        [Parameter(Mandatory)][DateTimeOffset]$Now,
        [int]$MinutesLocked = 20
    )
    if (-not $Allowed -or -not $RebootRequired -or $null -eq $LockedSince) { return $false }
    if (($Now - (ConvertTo-KcDate $LockedSince)).TotalMinutes -lt $MinutesLocked) { return $false }
    $last = ConvertTo-KcDate $LastRestartAt
    if ($null -ne $last -and ($Now - $last).TotalHours -lt 12) { return $false }
    $true
}

function Get-KcWindowsVersion {
    <# @{ name; version; build } e.g. Windows 11 Home, 24H2, 26100.4652. #>
    $r = [ordered]@{ name = 'Windows'; version = $null; build = $null; edition = $null }
    try {
        $cv = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop
        $build = [int](Get-KcProp $cv 'CurrentBuild' 0)
        $name = [string](Get-KcProp $cv 'ProductName' 'Windows')
        # Windows 11 still calls itself "Windows 10" in this registry value.
        if ($build -ge 22000) { $name = $name -replace 'Windows 10', 'Windows 11' }
        $r.name = $name
        $r.version = [string](Get-KcProp $cv 'DisplayVersion' (Get-KcProp $cv 'ReleaseId' ''))
        $r.build = '{0}.{1}' -f $build, (Get-KcProp $cv 'UBR' 0)
        $r.edition = [string](Get-KcProp $cv 'EditionID' '')
    } catch { }
    $r
}

function ConvertTo-KcUtcIso {
    <# Windows Update reports its dates in UTC without saying so. #>
    param($Value)
    if ($Value -isnot [datetime]) { return $null }
    if ($Value.Year -lt 2000) { return $null }
    ConvertTo-KcIso ([DateTimeOffset]([datetime]::SpecifyKind($Value, [DateTimeKind]::Utc)))
}

function Get-KcUpdateInfo {
    <# The quick facts about Windows Update: restart needed, paused, switched off, last search and install. Never throws. #>
    param([DateTimeOffset]$Now = (Get-KcNow))
    $r = [ordered]@{
        windows             = (Get-KcWindowsVersion)
        reboot_required     = $false
        paused_until        = $null
        service_disabled    = $false
        last_search_at      = $null
        last_installed_at   = $null
    }
    try { $r.reboot_required = [bool](New-Object -ComObject Microsoft.Update.SystemInfo).RebootRequired } catch {
        $r.reboot_required = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
    }
    try {
        $ux = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings' -ErrorAction Stop
        $until = ConvertTo-KcDate (Get-KcProp $ux 'PauseUpdatesExpiryTime')
        if ($null -ne $until -and $until -gt $Now) { $r.paused_until = ConvertTo-KcIso $until }
    } catch { }
    try {
        $svc = Get-CimInstance Win32_Service -Filter "Name='wuauserv'" -ErrorAction Stop
        $r.service_disabled = ([string]$svc.StartMode -eq 'Disabled')
    } catch { }
    try {
        $res = (New-Object -ComObject Microsoft.Update.AutoUpdate).Results
        $r.last_search_at = ConvertTo-KcUtcIso $res.LastSearchSuccessDate
        $r.last_installed_at = ConvertTo-KcUtcIso $res.LastInstallationSuccessDate
    } catch { }
    $r
}

function Find-KcWindowsUpdates {
    <# Search Windows Update (slow: run it in Security.ps1). Returns @{ Session; Found (COM updates); List (plain entries) }. #>
    $session = New-Object -ComObject Microsoft.Update.Session
    $session.ClientApplicationID = 'Kidputer Control'
    $result = $session.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0 and Type='Software'")
    $found = New-Object System.Collections.ArrayList
    $list = New-Object System.Collections.ArrayList
    foreach ($u in $result.Updates) {
        $cats = @(foreach ($c in $u.Categories) { [string]$c.Name })
        $kbs = @(foreach ($k in $u.KBArticleIDs) { [string]$k })
        $title = [string]$u.Title
        [void]$found.Add($u)
        [void]$list.Add([ordered]@{
                title      = $title
                kb         = $(if ($kbs.Count) { 'KB' + $kbs[0] } else { $null })
                kind       = (Get-KcUpdateKind -Categories $cats -Title $title)
                size       = [long]$u.MaxDownloadSize
                severity   = [string]$u.MsrcSeverity
                downloaded = [bool]$u.IsDownloaded
            })
    }
    @{ Session = $session; Found = $found; List = $list }
}

function Install-KcWindowsUpdates {
    <# Download and install what's waiting (slow: run it in Security.ps1). #>
    param([bool]$IncludeFeature)
    $search = Find-KcWindowsUpdates
    $coll = New-Object -ComObject Microsoft.Update.UpdateColl
    for ($i = 0; $i -lt $search.Found.Count; $i++) {
        if (-not (Test-KcUpdateWanted -Kind $search.List[$i].kind -IncludeFeature $IncludeFeature)) { continue }
        $u = $search.Found[$i]
        # Updates that ask the user questions while installing would hang with nobody there.
        if ($u.InstallationBehavior.CanRequestUserInput) { continue }
        if (-not $u.EulaAccepted) { $u.AcceptEula() }
        [void]$coll.Add($u)
    }
    $out = [ordered]@{ installed = @(); failed = @(); reboot_required = $false; updates = @($search.List) }
    if ($coll.Count -eq 0) { return $out }
    $dl = $search.Session.CreateUpdateDownloader()
    $dl.Updates = $coll
    [void]$dl.Download()
    $ready = New-Object -ComObject Microsoft.Update.UpdateColl
    foreach ($u in $coll) { if ($u.IsDownloaded) { [void]$ready.Add($u) } else { $out.failed += [string]$u.Title } }
    if ($ready.Count -eq 0) { return $out }
    $inst = $search.Session.CreateUpdateInstaller()
    $inst.Updates = $ready
    $res = $inst.Install()
    for ($i = 0; $i -lt $ready.Count; $i++) {
        # 2 = succeeded, 3 = succeeded with errors, 4 = failed, 5 = aborted.
        $code = [int]$res.GetUpdateResult($i).ResultCode
        if ($code -eq 2 -or $code -eq 3) { $out.installed += [string]$ready.Item($i).Title } else { $out.failed += [string]$ready.Item($i).Title }
    }
    $out.reboot_required = [bool]$res.RebootRequired
    $out
}

Export-ModuleMember -Function *-Kc*
