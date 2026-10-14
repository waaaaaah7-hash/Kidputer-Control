#Requires -Version 5.1
<#
    Apps on the child's PC: listing what runs in the child's session (roadmap #1) and closing an app
    remotely (#2). Windows PowerShell 5.1 compatible, plain ASCII.

    The pure functions (no Windows calls) take plain objects so the tests can run anywhere.
#>

Set-StrictMode -Version 3.0

# Processes that must never be closed from the dashboard: Windows itself and Kidputer Control.
$script:ProtectedNames = @(
    'explorer', 'csrss', 'winlogon', 'wininit', 'dwm', 'lsass', 'services', 'smss', 'svchost', 'sihost',
    'ctfmon', 'fontdrvhost', 'taskhostw', 'runtimebroker', 'shellexperiencehost', 'startmenuexperiencehost',
    'searchhost', 'searchapp', 'textinputhost', 'conhost', 'logonui', 'userinit', 'dllhost', 'audiodg',
    'securityhealthsystray', 'lockapp', 'applicationframehost', 'system', 'idle', 'registry', 'memory compression'
)

# Background processes that are part of Windows: hidden in the dashboard unless asked for.
function Test-KcSystemProcess {
    param([string]$Name, [string]$Path, [string]$WindowsDir = $env:SystemRoot)
    if (-not $Path) { return $true }   # path unreadable: system or protected process
    if ($WindowsDir -and $Path.StartsWith($WindowsDir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $true }
    $script:ProtectedNames -contains $Name.ToLowerInvariant()
}

function Test-KcProtectedProcess {
    <# True for processes the dashboard may not close: Windows' own, and Kidputer Control's. #>
    param([string]$Name, [string]$Path, [string]$WindowsDir = $env:SystemRoot, [string]$InstallRoot)
    if ($script:ProtectedNames -contains ([string]$Name).ToLowerInvariant()) { return $true }
    if (-not $Path) { return $true }
    if ($WindowsDir -and $Path.StartsWith($WindowsDir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $true }
    if ($InstallRoot -and $Path.StartsWith($InstallRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $true }
    $false
}

function Select-KcProcessesToStop {
    <#
        From a process list (objects with Id, Name, Path), the ones to close for "close <Path>": every
        copy of that program, except protected processes. Path comparison ignores case.
    #>
    param([object[]]$Processes, [Parameter(Mandatory)][string]$Path, [string]$WindowsDir = $env:SystemRoot, [string]$InstallRoot)
    , @($Processes | Where-Object {
            $_.Path -and [string]::Equals([string]$_.Path, $Path, [StringComparison]::OrdinalIgnoreCase) -and
            -not (Test-KcProtectedProcess -Name $_.Name -Path $_.Path -WindowsDir $WindowsDir -InstallRoot $InstallRoot)
        })
}

$script:VersionInfo = @{}

function Get-KcFileVersionInfo {
    <# Product name, description, publisher and original file name of a program; cached per path. #>
    param([Parameter(Mandatory)][string]$Path)
    if ($script:VersionInfo.ContainsKey($Path)) { return $script:VersionInfo[$Path] }
    $info = @{ product = $null; description = $null; company = $null; original_name = $null }
    try {
        $v = [Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
        $info.product = $v.ProductName
        $info.description = $v.FileDescription
        $info.company = $v.CompanyName
        $info.original_name = $v.OriginalFilename
    } catch { }
    if ($script:VersionInfo.Count -gt 2000) { $script:VersionInfo.Clear() }
    $script:VersionInfo[$Path] = $info
    $info
}

function Get-KcSessionProcesses {
    <#
        Processes in one Windows session (the child's), as plain objects:
        Id, Name, Path, StartTime, MemoryMB, HasWindow, Title.
    #>
    param([Parameter(Mandatory)][int]$SessionId, [switch]$IncludeTitles)
    $out = New-Object System.Collections.ArrayList
    foreach ($p in [Diagnostics.Process]::GetProcesses()) {
        try {
            if ($p.SessionId -ne $SessionId) { continue }
            $path = $null
            try { $path = [KcNative]::ProcessPath($p.Id) } catch { }
            $start = $null
            try { $start = ConvertTo-KcIso ([DateTimeOffset]$p.StartTime) } catch { }
            $hasWindow = $false
            $title = $null
            try {
                $hasWindow = $p.MainWindowHandle -ne [IntPtr]::Zero
                if ($hasWindow -and $IncludeTitles) { $title = $p.MainWindowTitle }
            } catch { }
            [void]$out.Add([pscustomobject]@{
                    Id        = $p.Id
                    Name      = $p.ProcessName
                    Path      = $path
                    StartTime = $start
                    MemoryMB  = [int]($p.WorkingSet64 / 1MB)
                    HasWindow = $hasWindow
                    Title     = $title
                })
        } catch { } finally { $p.Dispose() }
    }
    , @($out)
}

function ConvertTo-KcProcessReport {
    <#
        The report the dashboard shows: one entry per process with its program's friendly name and
        publisher. $VersionLookup maps a path to version info (tests pass a stub).
    #>
    param([object[]]$Processes, [scriptblock]$VersionLookup = { param($p) Get-KcFileVersionInfo -Path $p }, [string]$WindowsDir = $env:SystemRoot)
    $list = New-Object System.Collections.ArrayList
    foreach ($p in @($Processes | Select-Object -First 400)) {
        $v = @{ product = $null; description = $null; company = $null }
        if ($p.Path) { $v = & $VersionLookup $p.Path }
        [void]$list.Add([ordered]@{
                pid         = $p.Id
                name        = $p.Name
                path        = $p.Path
                started_at  = $p.StartTime
                memory_mb   = $p.MemoryMB
                has_window  = [bool]$p.HasWindow
                title       = $p.Title
                description = $v.description
                product     = $v.product
                company     = $v.company
                system      = (Test-KcSystemProcess -Name $p.Name -Path $p.Path -WindowsDir $WindowsDir)
            })
    }
    , @($list)
}

function Get-KcAppDisplayName {
    <# What to call an app on screen: its description ("Minecraft Launcher"), else its exe name. #>
    param($Process, $VersionInfo)
    $d = $null
    if ($VersionInfo) { $d = $VersionInfo.description; if (-not $d) { $d = $VersionInfo.product } }
    if ($d) { return [string]$d }
    [string]$Process.Name
}

# --- App rules (roadmap #3, #4) and usage (#5) -----------------------------------------------------------

function Get-KcSessionProcessList {
    <# A light list of the processes in one session (Id, Name, Path, StartTime), fast enough to run every second. #>
    param([Parameter(Mandatory)][int]$SessionId)
    $out = New-Object System.Collections.ArrayList
    foreach ($p in [Diagnostics.Process]::GetProcesses()) {
        try {
            if ($p.SessionId -ne $SessionId) { continue }
            $path = $null
            try { $path = [KcNative]::ProcessPath($p.Id) } catch { }
            $start = ''
            try { $start = $p.StartTime.ToUniversalTime().Ticks.ToString() } catch { }
            [void]$out.Add([pscustomobject]@{ Id = $p.Id; Name = $p.ProcessName; Path = $path; StartTime = $start })
        } catch { } finally { $p.Dispose() }
    }
    , @($out)
}

function New-KcAppIdentity {
    <# What a running program is, for matching: file name, original file name, product, publisher, path, command line. #>
    param([string]$Path, $VersionInfo, [string]$CommandLine)
    $exe = ''
    if ($Path) { $exe = ($Path -split '[\\/]')[-1] }   # either separator, so tests on Linux see Windows paths right
    $v = $VersionInfo
    @{
        exe      = $exe
        original = [string](Get-KcProp ([pscustomobject]$v) 'original_name' '')
        product  = [string](Get-KcProp ([pscustomobject]$v) 'product' '')
        company  = [string](Get-KcProp ([pscustomobject]$v) 'company' '')
        path     = [string]$Path
        cmdline  = [string]$CommandLine
    }
}

function Test-KcAppMatcher {
    <#
        One catalogue matcher against a program. Every field given must match: exe (the file name or the
        original file name inside the program, so renamed copies still match), and text found in the
        product, publisher, path or command line. Case is ignored. An empty matcher matches nothing.
    #>
    param([Parameter(Mandatory)][hashtable]$Identity, $Matcher)
    $any = $false
    foreach ($field in 'exe', 'product', 'company', 'path', 'cmdline') {
        $want = [string](Get-KcProp $Matcher $field '')
        if (-not $want) { continue }
        $any = $true
        if ($field -eq 'exe') {
            $ok = [string]::Equals($want, $Identity.exe, [StringComparison]::OrdinalIgnoreCase) -or
                  ($Identity.original -and [string]::Equals($want, $Identity.original, [StringComparison]::OrdinalIgnoreCase))
            if (-not $ok) { return $false }
        } else {
            $have = [string]$Identity[$field]
            if (-not $have -or $have.IndexOf($want, [StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
        }
    }
    $any
}

function Find-KcCatalogApp {
    <# The catalogue app a program is, or $null. The parents' own (custom) apps are checked first. #>
    param([Parameter(Mandatory)][hashtable]$Identity, [object[]]$Catalog)
    $ordered = @(@($Catalog) | Where-Object { ([string](Get-KcProp $_ 'id' '')).StartsWith('custom:') }) +
               @(@($Catalog) | Where-Object { -not ([string](Get-KcProp $_ 'id' '')).StartsWith('custom:') })
    foreach ($app in $ordered) {
        foreach ($m in @(Get-KcProp $app 'matchers' @())) {
            if (Test-KcAppMatcher -Identity $Identity -Matcher $m) { return $app }
        }
    }
    $null
}

function Get-KcAppRule {
    <# The rule that applies to an app: its own rule if it has one, else its category's. #>
    param($App, [object[]]$Rules)
    if ($null -eq $App) { return $null }
    $id = [string](Get-KcProp $App 'id' '')
    $cat = [string](Get-KcProp $App 'category' '')
    foreach ($r in @($Rules)) {
        if ((Get-KcProp $r 'target_kind') -eq 'app' -and (Get-KcProp $r 'target') -eq $id) { return $r }
    }
    foreach ($r in @($Rules)) {
        if ((Get-KcProp $r 'target_kind') -eq 'category' -and (Get-KcProp $r 'target') -eq $cat) { return $r }
    }
    $null
}

function Test-KcAppAllowed {
    <#
        Is an app with this rule allowed right now? Returns @{Allowed; Until; NextStart}.
        Schedules use the server's precomputed UTC windows while they're fresh ($UseWeekly is for
        offline > 14 days: the weekly local-time windows on this PC's clock).
    #>
    param($Rule, [Parameter(Mandatory)][DateTimeOffset]$Now, [switch]$UseWeekly, [TimeZoneInfo]$TimeZone = [TimeZoneInfo]::Local)
    if ($null -eq $Rule) { return @{ Allowed = $true; Until = $null; NextStart = $null } }
    switch ([string](Get-KcProp $Rule 'mode' 'allow')) {
        'allow' { return @{ Allowed = $true; Until = $null; NextStart = $null } }
        'block' { return @{ Allowed = $false; Until = $null; NextStart = $null } }
    }
    if ($UseWeekly) {
        $entries = @(@(Get-KcProp $Rule 'windows' @()) | ForEach-Object { [pscustomobject]@{ day_of_week = $_.day; lock_at = $_.start; unlock_at = $_.end } })
        $w = Get-KcWeeklyWindow -Now $Now -Schedule $entries -TimeZone $TimeZone
        if ($w) { return @{ Allowed = $true; Until = $w.End; NextStart = $null } }
        return @{ Allowed = $false; Until = $null; NextStart = $null }
    }
    $next = $null
    foreach ($w in @(Get-KcProp $Rule 'allowed' @())) {
        $s = ConvertTo-KcDate (Get-KcProp $w 'start')
        $e = ConvertTo-KcDate (Get-KcProp $w 'end')
        if ($null -eq $s -or $null -eq $e) { continue }
        if ($s -le $Now -and $Now -lt $e) { return @{ Allowed = $true; Until = $e; NextStart = $null } }
        if ($s -gt $Now -and ($null -eq $next -or $s -lt $next)) { $next = $s }
    }
    @{ Allowed = $false; Until = $null; NextStart = $next }
}

function Get-KcBlockedMessage {
    <# What the child sees when an app is closed by a rule. #>
    param([string]$Name, $Decision)
    if ($Decision.NextStart) {
        $local = $Decision.NextStart.ToLocalTime()
        $when = $local.ToString('HH:mm')
        if ($local.Date -ne [DateTime]::Now.Date) { $when = $local.ToString('ddd HH:mm') }
        return "$Name isn't allowed right now. It's available from $when."
    }
    "$Name isn't allowed on this PC."
}

function Get-KcUsageDay {
    <# Which local day (from the server's day_starts) an instant falls in, as 'yyyy-MM-dd'; $null if unknown. #>
    param([Parameter(Mandatory)][DateTimeOffset]$Now, [object[]]$DayStarts)
    $best = $null
    foreach ($d in @($DayStarts)) {
        $s = ConvertTo-KcDate (Get-KcProp $d 'start')
        if ($null -ne $s -and $s -le $Now) { $best = [string](Get-KcProp $d 'day') }
    }
    $best
}

function Add-KcUsage {
    <#
        Add seconds to one app's total for a day. $Usage: hashtable day -> hashtable key -> @{name;
        category; seconds; path}. Days older than the newest 8 are dropped.
    #>
    param([Parameter(Mandatory)][hashtable]$Usage, [Parameter(Mandatory)][string]$Day, [Parameter(Mandatory)][string]$Key,
          [string]$Name, [string]$Category, [string]$Path, [int]$Seconds)
    if (-not $Usage.ContainsKey($Day)) { $Usage[$Day] = @{} }
    $d = $Usage[$Day]
    if (-not $d.ContainsKey($Key)) { $d[$Key] = @{ name = $Name; category = $Category; seconds = 0; path = $Path } }
    $d[$Key].seconds = [int]$d[$Key].seconds + $Seconds
    if ($Usage.Count -gt 8) {
        foreach ($old in @($Usage.Keys | Sort-Object | Select-Object -First ($Usage.Count - 8))) { $Usage.Remove($old) }
    }
}

Export-ModuleMember -Function *-Kc*
