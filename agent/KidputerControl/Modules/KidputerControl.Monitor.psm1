#Requires -Version 5.1
<#
    Change monitoring (roadmap #17): what changed on the PC since last time, reported as events that
    the server turns into parent notifications.

      - apps installed or removed (Windows' installed-programs list, machine-wide and the child's own,
        plus Store apps)
      - programs run for the first time (portable games, things run straight from a zip)
      - new archive files in Downloads, Desktop and Documents, and archives being opened
      - Windows accounts created or deleted, and administrator changes
      - Windows Defender real-time protection switched off or on
      - USB drives connected
      - VPN and remote-access apps

    The pure functions (comparing lists, classifying paths) take plain objects so the tests run
    anywhere; the Get-* collectors read Windows. Windows PowerShell 5.1 compatible, plain ASCII.
#>

Set-StrictMode -Version 3.0

# --- Pure helpers ---------------------------------------------------------------------------------------

function Get-MonField($Object, [string]$Name) {
    # Fresh data is a hashtable; data read back from monitor.json is a PSCustomObject.
    if ($null -eq $Object) { return $null }
    if ($Object -is [Collections.IDictionary]) { return $Object[$Name] }
    Get-KcProp $Object $Name
}

function Compare-KcKeyed {
    <#
        Compare two lists of objects by a key. Returns @{Added; Removed} (objects from New / Old).
        Keys compare without case.
    #>
    param([object[]]$Old, [object[]]$New, [Parameter(Mandatory)][string]$Key)
    $oldKeys = @{}
    foreach ($o in @($Old)) { if ($null -ne $o) { $oldKeys[([string]$o.$Key).ToLowerInvariant()] = $o } }
    $newKeys = @{}
    foreach ($n in @($New)) { if ($null -ne $n) { $newKeys[([string]$n.$Key).ToLowerInvariant()] = $n } }
    $added = @($newKeys.Keys | Where-Object { -not $oldKeys.ContainsKey($_) } | Sort-Object | ForEach-Object { $newKeys[$_] })
    $removed = @($oldKeys.Keys | Where-Object { -not $newKeys.ContainsKey($_) } | Sort-Object | ForEach-Object { $oldKeys[$_] })
    @{ Added = $added; Removed = $removed }
}

function ConvertTo-KcProgramKey {
    <#
        A program's identity for "first run" purposes, stable across updates: the lower-cased path with
        version-numbered folders made generic, so Chrome moving to ...\Application\131.0.1\ or Discord to
        ...\app-1.0.9\ isn't a "new program".
    #>
    param([Parameter(Mandatory)][string]$Path)
    $p = $Path.ToLowerInvariant().Replace('/', '\')
    $p = [regex]::Replace($p, '\\(app-|v)?\d+(\.\d+)+[^\\]*\\', '\*\')
    $p = [regex]::Replace($p, '\\\{?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}?\\', '\*\')
    $p
}

$script:ArchiveExtensions = @('.zip', '.rar', '.7z', '.tar', '.gz', '.tgz', '.cab', '.iso')

function Test-KcArchiveName {
    param([string]$Name)
    if (-not $Name) { return $false }
    $script:ArchiveExtensions -contains ([IO.Path]::GetExtension($Name).ToLowerInvariant())
}

function Get-KcRunOrigin {
    <#
        Where a newly run program came from: 'archive' (run straight out of a zip/rar/7z, which Windows
        and archive tools unpack into a temp folder), 'usb', 'downloads', 'temp', or $null (ordinary).
    #>
    param([Parameter(Mandatory)][string]$Path, [string[]]$RemovableDrives = @())
    $p = $Path.ToLowerInvariant().Replace('/', '\')
    if ($p -match '\\temp\\(.*\.(zip|rar|7z)\\|rar\$|7z[a-z0-9]{4,}\\|temp\d+_[^\\]*\.zip\\)') { return 'archive' }
    foreach ($d in @($RemovableDrives)) {
        if ($d -and $p.StartsWith(($d.TrimEnd('\') + '\').ToLowerInvariant())) { return 'usb' }
    }
    if ($p -match '\\downloads\\') { return 'downloads' }
    if ($p -match '\\appdata\\local\\temp\\') { return 'temp' }
    $null
}

# VPNs and remote-control apps: the usual ways around home filtering, or of someone else taking over the PC.
$script:FlaggedApps = @(
    @{ kind = 'vpn'; pattern = 'nordvpn|expressvpn|protonvpn|proton vpn|surfshark|cyberghost|windscribe|tunnelbear|hotspot shield|private internet access|mullvad|openvpn|wireguard|psiphon|hola vpn|hide\.me|ipvanish|atlas vpn|urban vpn|tor browser|ultrasurf|zenmate|betternet|turbo vpn' },
    @{ kind = 'remote_access'; pattern = 'anydesk|teamviewer|rustdesk|chrome remote desktop|parsec|splashtop|ultraviewer|realvnc|tightvnc|ultravnc|ammyy|supremo|remotepc|logmein|dwservice|getscreen' }
)

function Find-KcFlaggedApp {
    <# 'vpn', 'remote_access', or $null for a program name / product / publisher. #>
    param([string[]]$Names)
    foreach ($n in @($Names)) {
        if (-not $n) { continue }
        $l = $n.ToLowerInvariant()
        foreach ($f in $script:FlaggedApps) { if ($l -match $f.pattern) { return $f.kind } }
    }
    $null
}

function Select-KcInstalledPrograms {
    <#
        Turn raw uninstall-registry entries (objects with DisplayName, Publisher, DisplayVersion,
        SystemComponent, ParentKeyName, ReleaseType) into the list worth reporting: named, not a
        Windows component, not an update or hotfix of something else. One entry per name.
    #>
    param([object[]]$Entries, [string]$Scope = 'machine')
    $seen = @{}
    $out = New-Object System.Collections.ArrayList
    foreach ($e in @($Entries)) {
        $name = [string](Get-KcProp $e 'DisplayName' '')
        if (-not $name.Trim()) { continue }
        if ([int](Get-KcProp $e 'SystemComponent' 0) -eq 1) { continue }
        if ([string](Get-KcProp $e 'ParentKeyName' '')) { continue }
        if (@('Update', 'Hotfix', 'Security Update') -contains [string](Get-KcProp $e 'ReleaseType' '')) { continue }
        $key = $name.Trim().ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        [void]$out.Add([pscustomobject]@{
                name      = $name.Trim()
                publisher = [string](Get-KcProp $e 'Publisher' '')
                version   = [string](Get-KcProp $e 'DisplayVersion' '')
                scope     = $Scope
            })
    }
    , @($out | Sort-Object name)
}

function Get-KcProgramChangeEvents {
    <#
        Events for installed-program changes between two lists (from Select-KcInstalledPrograms). A
        VPN or remote-access app also gets its own event, so parents can be told about those separately.
        Returns hashtables ready for Add-KcEvent.
    #>
    param([object[]]$Old, [object[]]$New)
    $diff = Compare-KcKeyed -Old $Old -New $New -Key 'name'
    $events = New-Object System.Collections.ArrayList
    foreach ($a in $diff.Added) {
        $label = $a.name
        if ($a.publisher) { $label = "$($a.name) ($($a.publisher))" }
        [void]$events.Add(@{ type = 'software.installed'; severity = 'info'; message = "Installed: $label"; data = @{ name = $a.name; publisher = $a.publisher; version = $a.version; scope = $a.scope } })
        $flag = Find-KcFlaggedApp -Names @($a.name, $a.publisher)
        if ($flag) {
            $what = 'VPN'
            if ($flag -eq 'remote_access') { $what = 'Remote-control app' }
            [void]$events.Add(@{ type = 'software.vpn_detected'; severity = 'warning'; message = "${what} installed: $($a.name)"; data = @{ name = $a.name; kind = $flag } })
        }
    }
    foreach ($r in $diff.Removed) {
        [void]$events.Add(@{ type = 'software.uninstalled'; severity = 'info'; message = "Removed: $($r.name)"; data = @{ name = $r.name; publisher = $r.publisher; scope = $r.scope } })
    }
    , @($events)
}

function Get-KcAccountChangeEvents {
    <#
        Events for Windows account changes. $Old/$New: @{ users = [{name, sid}]; admins = [sid] }.
    #>
    param($Old, $New)
    $events = New-Object System.Collections.ArrayList
    $users = Compare-KcKeyed -Old @(Get-MonField $Old 'users') -New @(Get-MonField $New 'users') -Key 'sid'
    foreach ($u in $users.Added) { [void]$events.Add(@{ type = 'account.created'; severity = 'warning'; message = "New Windows account: $($u.name)"; data = @{ name = $u.name; sid = $u.sid } }) }
    foreach ($u in $users.Removed) { [void]$events.Add(@{ type = 'account.deleted'; severity = 'info'; message = "Windows account deleted: $($u.name)"; data = @{ name = $u.name; sid = $u.sid } }) }
    $names = @{}
    foreach ($u in @(Get-MonField $New 'users') + @(Get-MonField $Old 'users')) { if ($u) { $names[[string]$u.sid] = [string]$u.name } }
    $oldAdmins = @(@(Get-MonField $Old 'admins') | Where-Object { $_ } | ForEach-Object { [pscustomobject]@{ sid = [string]$_ } })
    $newAdmins = @(@(Get-MonField $New 'admins') | Where-Object { $_ } | ForEach-Object { [pscustomobject]@{ sid = [string]$_ } })
    $admins = Compare-KcKeyed -Old $oldAdmins -New $newAdmins -Key 'sid'
    foreach ($a in $admins.Added) {
        $n = $names[$a.sid]; if (-not $n) { $n = $a.sid }
        [void]$events.Add(@{ type = 'account.admin_added'; severity = 'warning'; message = "$n is now an administrator"; data = @{ name = $n; sid = $a.sid } })
    }
    foreach ($a in $admins.Removed) {
        $n = $names[$a.sid]; if (-not $n) { $n = $a.sid }
        [void]$events.Add(@{ type = 'account.admin_removed'; severity = 'info'; message = "$n is no longer an administrator"; data = @{ name = $n; sid = $a.sid } })
    }
    , @($events)
}

function Get-KcFirstRunEvent {
    <#
        The event for a program seen running for the first time, or $null if it isn't new (or is part
        of Windows / Kidputer Control). Updates $Seen (hashtable key -> first-seen time). While
        $Learning (the first days after install, when everything is new) it records quietly instead.
    #>
    param(
        [Parameter(Mandatory)]$Process, [Parameter(Mandatory)][hashtable]$Seen, [string]$Now,
        [string[]]$RemovableDrives = @(), [string]$WindowsDir = $env:SystemRoot, [string]$InstallRoot,
        $VersionInfo, [switch]$Learning
    )
    $path = [string]$Process.Path
    if (-not $path) { return $null }
    if ($WindowsDir -and $path.StartsWith($WindowsDir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $null }
    if ($InstallRoot -and $path.StartsWith($InstallRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $null }
    $key = ConvertTo-KcProgramKey -Path $path
    if ($Seen.ContainsKey($key)) { return $null }
    $Seen[$key] = $Now
    if ($Learning) { return $null }
    $name = [string]$Process.Name
    $product = $null; $company = $null
    if ($VersionInfo) {
        if ($VersionInfo.description) { $name = [string]$VersionInfo.description } elseif ($VersionInfo.product) { $name = [string]$VersionInfo.product }
        $product = $VersionInfo.product; $company = $VersionInfo.company
    }
    $origin = Get-KcRunOrigin -Path $path -RemovableDrives $RemovableDrives
    $where = ''
    switch ($origin) {
        'archive' { $where = ' straight from a zip or other archive' }
        'usb' { $where = ' from a USB drive' }
        'downloads' { $where = ' from Downloads' }
        'temp' { $where = ' from a temporary folder' }
    }
    $severity = 'info'
    if ($origin -eq 'archive' -or $origin -eq 'usb') { $severity = 'warning' }
    $ev = @{ type = 'program.first_run'; severity = $severity; message = "First run of $name$where"; data = @{ path = $path; name = $name; product = $product; company = $company; origin = $origin } }
    $flag = Find-KcFlaggedApp -Names @($name, $product, $company, [IO.Path]::GetFileNameWithoutExtension($path))
    if ($flag) { $ev.data.flagged = $flag }
    $ev
}

function Limit-KcSeenPrograms {
    <# Keep the first-run memory bounded: drop the oldest entries beyond $Max. #>
    param([Parameter(Mandatory)][hashtable]$Seen, [int]$Max = 5000)
    if ($Seen.Count -le $Max) { return }
    $drop = @($Seen.GetEnumerator() | Sort-Object Value | Select-Object -First ($Seen.Count - $Max) | ForEach-Object { $_.Key })
    foreach ($k in $drop) { $Seen.Remove($k) }
}

# --- Windows collectors ------------------------------------------------------------------------------------

function Get-KcUninstallEntries {
    <# Raw uninstall entries under one registry root (e.g. 'HKLM:\SOFTWARE'). #>
    param([Parameter(Mandatory)][string]$Root)
    $path = Join-Path $Root 'Microsoft\Windows\CurrentVersion\Uninstall'
    if (-not (Test-Path -LiteralPath $path)) { return , @() }
    , @(Get-ChildItem -LiteralPath $path -ErrorAction SilentlyContinue | ForEach-Object {
            try { Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction Stop } catch { }
        })
}

function Get-KcMachinePrograms {
    $entries = @(Get-KcUninstallEntries -Root 'HKLM:\SOFTWARE') + @(Get-KcUninstallEntries -Root 'HKLM:\SOFTWARE\WOW6432Node')
    Select-KcInstalledPrograms -Entries $entries -Scope 'machine'
}

function Get-KcUserPrograms {
    <# Programs installed just for the child (only readable while the child's registry is loaded). #>
    param([Parameter(Mandatory)][string]$Sid)
    $root = "Registry::HKEY_USERS\$Sid\Software"
    if (-not (Test-Path -LiteralPath $root)) { return $null }
    Select-KcInstalledPrograms -Entries (Get-KcUninstallEntries -Root $root) -Scope 'user'
}

function Get-KcStoreApps {
    <# Microsoft Store apps installed for the child (not frameworks or built-in system apps). #>
    param([Parameter(Mandatory)][string]$Sid)
    try {
        , @(Get-AppxPackage -User $Sid -ErrorAction Stop | Where-Object { -not $_.IsFramework -and -not $_.NonRemovable -and $_.SignatureKind -ne 'System' } |
                ForEach-Object { [pscustomobject]@{ name = [string]$_.Name; publisher = [string]$_.PublisherId; version = [string]$_.Version; scope = 'store' } } |
                Sort-Object name)
    } catch { $null }
}

function Get-KcAccounts {
    try {
        $users = @(Get-LocalUser -ErrorAction Stop | ForEach-Object { [pscustomobject]@{ name = [string]$_.Name; sid = [string]$_.SID.Value } })
        $admins = @(Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop | Where-Object { $_.SID } | ForEach-Object { [string]$_.SID.Value })
        @{ users = $users; admins = $admins }
    } catch { $null }
}

function Get-KcDefenderState {
    <# @{ realtime; antivirus } or $null when Defender isn't the antivirus (or can't be read). #>
    try {
        $s = Get-MpComputerStatus -ErrorAction Stop
        @{ realtime = [bool]$s.RealTimeProtectionEnabled; antivirus = [bool]$s.AntivirusEnabled }
    } catch { $null }
}

function Get-KcRemovableDrives {
    , @([IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq [IO.DriveType]::Removable -and $_.IsReady } | ForEach-Object {
            [pscustomobject]@{ name = $_.Name; label = [string]$_.VolumeLabel; size = [long]$_.TotalSize; free = [long]$_.AvailableFreeSpace }
        })
}

function Get-KcDriveSummary {
    <# A quick look at what's on a USB drive: programs and archives in the top two folder levels. #>
    param([Parameter(Mandatory)][string]$Root, [int]$MaxEntries = 3000)
    $programs = 0; $archives = 0; $seen = 0
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue(@{ Path = $Root; Depth = 0 })
    while ($queue.Count -and $seen -lt $MaxEntries) {
        $item = $queue.Dequeue()
        try { $entries = [IO.Directory]::GetFileSystemEntries($item.Path) } catch { continue }
        foreach ($e in $entries) {
            $seen++
            if ($seen -ge $MaxEntries) { break }
            $ext = [IO.Path]::GetExtension($e).ToLowerInvariant()
            if (@('.exe', '.msi', '.bat', '.cmd', '.ps1') -contains $ext) { $programs++ }
            elseif ($script:ArchiveExtensions -contains $ext) { $archives++ }
            elseif ($item.Depth -lt 1 -and [IO.Directory]::Exists($e)) { $queue.Enqueue(@{ Path = $e; Depth = $item.Depth + 1 }) }
        }
    }
    @{ programs = $programs; archives = $archives; partial = ($seen -ge $MaxEntries) }
}

function Get-KcProfilePath {
    param([Parameter(Mandatory)][string]$Sid)
    try {
        $p = Get-CimInstance Win32_UserProfile -Filter "SID = '$($Sid -replace "'", '')'" -ErrorAction Stop
        if ($p) { return [string]$p.LocalPath }
    } catch { }
    $null
}

function Get-KcArchiveFiles {
    <# Archive files in the child's Downloads, Desktop and Documents (and one folder level down). #>
    param([Parameter(Mandatory)][string]$ProfilePath, [int]$Max = 2000)
    $out = New-Object System.Collections.ArrayList
    foreach ($sub in 'Downloads', 'Desktop', 'Documents') {
        $dir = Join-Path $ProfilePath $sub
        if (-not [IO.Directory]::Exists($dir)) { continue }
        $dirs = @($dir)
        try { $dirs += @([IO.Directory]::GetDirectories($dir) | Select-Object -First 200) } catch { }
        foreach ($d in $dirs) {
            try { $files = [IO.Directory]::GetFiles($d) } catch { continue }
            foreach ($f in $files) {
                if ((Test-KcArchiveName $f) -and $out.Count -lt $Max) {
                    $size = 0L
                    try { $size = ([IO.FileInfo]$f).Length } catch { }
                    [void]$out.Add([pscustomobject]@{ path = $f; size = $size })
                }
            }
        }
    }
    , @($out)
}

function Get-KcRecentArchives {
    <# Archives the child opened in Explorer, from Windows' Recent Items (shortcuts like game.zip.lnk). #>
    param([Parameter(Mandatory)][string]$ProfilePath)
    $dir = Join-Path $ProfilePath 'AppData\Roaming\Microsoft\Windows\Recent'
    if (-not [IO.Directory]::Exists($dir)) { return , @() }
    , @([IO.Directory]::GetFiles($dir, '*.lnk') | Where-Object { Test-KcArchiveName ([IO.Path]::GetFileNameWithoutExtension($_)) } |
            ForEach-Object { [pscustomobject]@{ path = [IO.Path]::GetFileNameWithoutExtension($_) } })
}

Export-ModuleMember -Function *-Kc*
