#Requires -Version 5.1
<#
    Shared logic for the Kidputer Control agent (watchdog + UI agent + installer).

    Targets Windows PowerShell 5.1 (what the SYSTEM task runs) and stays PowerShell 7 compatible:
    no ternaries / null-coalescing, all file I/O through .NET with BOM-less UTF-8, and dates are
    normalised through ConvertTo-KcDate because PS7's ConvertFrom-Json turns ISO strings into
    [datetime] while 5.1 leaves them as strings.
#>

Set-StrictMode -Version 3.0

$script:AgentVersion = '1.0.0'
$script:Utf8 = New-Object System.Text.UTF8Encoding($false)
$script:Paths = $null
$script:Http = $null

# --- Paths -----------------------------------------------------------------------------------------

function Initialize-KcPaths {
    <# Resolve the on-disk layout. Tests pass a temp root; production uses %ProgramData%\KidputerControl. #>
    param([string]$DataRoot = (Join-Path $env:ProgramData 'KidputerControl'))
    $script:Paths = [pscustomobject]@{
        DataRoot   = $DataRoot
        Config     = Join-Path $DataRoot 'config'      # SYSTEM + Administrators only
        Logs       = Join-Path $DataRoot 'logs'        # SYSTEM + Administrators only
        Ipc        = Join-Path $DataRoot 'ipc'         # SYSTEM writes, Users read
        Inbox      = Join-Path $DataRoot 'ipc\inbox'   # Users may only drop files in
        AgentFile  = Join-Path $DataRoot 'config\agent.json'
        TokenFile  = Join-Path $DataRoot 'config\device.token'
        StateFile  = Join-Path $DataRoot 'config\state.json'
        LocalFile  = Join-Path $DataRoot 'config\local.json'
        QueueFile  = Join-Path $DataRoot 'config\queue.jsonl'
        Manifest   = Join-Path $DataRoot 'config\manifest.json'
        UiState    = Join-Path $DataRoot 'ipc\ui-state.json'
    }
    $script:Paths
}

function Get-KcPaths {
    if ($null -eq $script:Paths) { Initialize-KcPaths | Out-Null }
    $script:Paths
}

function Get-KcAgentVersion { $script:AgentVersion }

function Test-KcIsWindows {
    # $IsWindows only exists on PowerShell 6+; 5.1 is Windows-only.
    $v = Get-Variable -Name IsWindows -ValueOnly -ErrorAction SilentlyContinue
    ($null -eq $v) -or $v
}

# --- JSON / files ------------------------------------------------------------------------------------

function Read-KcJsonFile {
    param([Parameter(Mandatory)][string]$Path)
    if (-not [IO.File]::Exists($Path)) { return $null }
    $text = [IO.File]::ReadAllText($Path, $script:Utf8)
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }
    $text | ConvertFrom-Json
}

function Move-KcFileOver {
    <# Rename $Source onto $Destination, replacing it atomically if it exists. #>
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination)
    if ([IO.File]::Exists($Destination)) {
        # [NullString]: a plain $null would be passed to .NET as "" (no backup file wanted).
        [IO.File]::Replace($Source, $Destination, [NullString]::Value)
    } else {
        [IO.File]::Move($Source, $Destination)
    }
}

function Write-KcJsonFile {
    <# Atomic replace so a concurrent reader (or a power cut) never sees a half-written file. #>
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Object)
    $json = ConvertTo-Json -InputObject $Object -Depth 12 -Compress
    $tmp = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($tmp, $json, $script:Utf8)
    Move-KcFileOver -Source $tmp -Destination $Path
}

function Get-KcProp {
    <# Null-safe property read for PSCustomObjects from ConvertFrom-Json (StrictMode-proof). #>
    param($Object, [Parameter(Mandatory)][string]$Name, $Default = $null)
    if ($null -eq $Object) { return $Default }
    $p = $Object.PSObject.Properties[$Name]
    if ($null -eq $p -or $null -eq $p.Value) { return $Default }
    $p.Value
}

# --- Time ---------------------------------------------------------------------------------------------

function ConvertTo-KcDate {
    <# ISO-8601 string or [datetime]/[DateTimeOffset] -> [DateTimeOffset], or $null. #>
    param($Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [DateTimeOffset]) { return $Value }
    if ($Value -is [datetime]) {
        if ($Value.Kind -eq [DateTimeKind]::Unspecified) { $Value = [datetime]::SpecifyKind($Value, [DateTimeKind]::Utc) }
        return [DateTimeOffset]$Value
    }
    $parsed = [DateTimeOffset]::MinValue
    $styles = [Globalization.DateTimeStyles]::AssumeUniversal
    if ([DateTimeOffset]::TryParse([string]$Value, [Globalization.CultureInfo]::InvariantCulture, $styles, [ref]$parsed)) {
        return $parsed
    }
    $null
}

function ConvertTo-KcIso {
    param([DateTimeOffset]$Value)
    $Value.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ', [Globalization.CultureInfo]::InvariantCulture)
}

function Get-KcNow { [DateTimeOffset]::UtcNow }

# --- Local state (PIN lockout, local overrides, message acks) -----------------------------------------

function New-KcLocalState {
    [pscustomobject]@{
        pin_failures       = 0
        pin_locked_until   = $null
        local_overrides    = @()   # [{starts_at, ends_at}] granted by the lock-screen PIN
        released_locks     = @()   # immediate-lock ids released by the PIN
        dismissed_messages = @()   # closable message ids the child closed
        displayed_messages = @()   # message ids already reported as displayed
        lock_episode       = $null # {key, countdown_ends_at}: survives reboots so a restart can't reset the countdown
    }
}

function Read-KcLocalState {
    param([string]$Path = (Get-KcPaths).LocalFile)
    $raw = $null
    try { $raw = Read-KcJsonFile -Path $Path } catch { $raw = $null }
    $state = New-KcLocalState
    if ($null -ne $raw) {
        foreach ($name in @($state.PSObject.Properties.Name)) {
            $v = Get-KcProp $raw $name
            if ($null -ne $v) { $state.$name = $v }
        }
        foreach ($list in 'local_overrides', 'released_locks', 'dismissed_messages', 'displayed_messages') {
            $state.$list = @($state.$list)
        }
    }
    $state
}

function Add-KcBoundedItem {
    <# Append to a list kept to the most recent $Max entries. #>
    param([object[]]$List, $Item, [int]$Max = 50)
    $all = @($List) + @($Item)
    if ($all.Count -gt $Max) { $all = $all[($all.Count - $Max)..($all.Count - 1)] }
    , $all
}

# --- Enforcement decision (pure) ------------------------------------------------------------------------

function Get-KcWeeklyWindow {
    <#
        Fallback when the precomputed UTC windows have run out (offline > 14 days): evaluate the weekly
        schedule in the PC's local time. Returns @{Start; End} of the window containing $Now, or $null.
    #>
    param([Parameter(Mandatory)][DateTimeOffset]$Now, [object[]]$Schedule, [TimeZoneInfo]$TimeZone = [TimeZoneInfo]::Local)
    $local = [TimeZoneInfo]::ConvertTime($Now, $TimeZone)
    foreach ($dayOffset in -1, 0) {
        $day = $local.Date.AddDays($dayOffset)
        $dow = (([int]$day.DayOfWeek) + 6) % 7   # Monday = 0, matching the server
        foreach ($e in @($Schedule)) {
            if ([int](Get-KcProp $e 'day_of_week' -1) -ne $dow) { continue }
            $lockAt = [TimeSpan]::Parse([string]$e.lock_at, [Globalization.CultureInfo]::InvariantCulture)
            $unlockAt = [TimeSpan]::Parse([string]$e.unlock_at, [Globalization.CultureInfo]::InvariantCulture)
            $startLocal = $day.Add($lockAt)
            $endLocal = $day.Add($unlockAt)
            if ($unlockAt -le $lockAt) { $endLocal = $endLocal.AddDays(1) }
            $start = New-Object DateTimeOffset($startLocal, $TimeZone.GetUtcOffset($startLocal))
            $end = New-Object DateTimeOffset($endLocal, $TimeZone.GetUtcOffset($endLocal))
            if ($start -le $Now -and $Now -lt $end) { return @{ Start = $start; End = $end } }
        }
    }
    $null
}

function Get-KcActiveOverride {
    param([DateTimeOffset]$Now, [object[]]$Overrides)
    $best = $null
    foreach ($o in @($Overrides)) {
        $s = ConvertTo-KcDate (Get-KcProp $o 'starts_at')
        $e = ConvertTo-KcDate (Get-KcProp $o 'ends_at')
        if ($null -eq $s -or $null -eq $e) { continue }
        if ($s -le $Now -and $Now -lt $e -and ($null -eq $best -or $e -gt $best)) { $best = $e }
    }
    $best
}

function Get-KcEnforcementDecision {
    <#
        Pure function: should the PC be locked right now, and why?
        Precedence: immediate lock > override (server or local PIN) > schedule.
        Returns @{Locked; Reason; EpisodeKey; UnlockAt; LockRequestId; OverrideEndsAt}.
    #>
    param(
        [Parameter(Mandatory)][DateTimeOffset]$Now,
        $State,
        $Local = (New-KcLocalState),
        [TimeZoneInfo]$TimeZone = [TimeZoneInfo]::Local
    )
    $d = @{ Locked = $false; Reason = 'none'; EpisodeKey = $null; UnlockAt = $null; LockRequestId = $null; OverrideEndsAt = $null }

    if ($null -eq $State) { $d.Reason = 'no_config'; return $d }
    if (-not (Get-KcProp $State 'enforcement_enabled' $true)) { $d.Reason = 'disabled'; return $d }

    $lock = Get-KcProp $State 'immediate_lock'
    if ($null -ne $lock) {
        $lockId = [string](Get-KcProp $lock 'id')
        if (@($Local.released_locks) -notcontains $lockId) {
            $d.Locked = $true; $d.Reason = 'immediate'; $d.LockRequestId = $lockId; $d.EpisodeKey = "immediate:$lockId"
            return $d
        }
    }

    $overrideEnd = Get-KcActiveOverride -Now $Now -Overrides (@(Get-KcProp $State 'overrides' @()) + @($Local.local_overrides))
    if ($null -ne $overrideEnd) { $d.Reason = 'override'; $d.OverrideEndsAt = $overrideEnd; return $d }

    $window = $null
    $horizon = ConvertTo-KcDate (Get-KcProp $State 'lock_windows_until')
    if ($null -ne $horizon -and $Now -lt $horizon) {
        foreach ($w in @(Get-KcProp $State 'lock_windows' @())) {
            $s = ConvertTo-KcDate (Get-KcProp $w 'start')
            $e = ConvertTo-KcDate (Get-KcProp $w 'end')
            if ($null -ne $s -and $null -ne $e -and $s -le $Now -and $Now -lt $e) { $window = @{ Start = $s; End = $e }; break }
        }
    } else {
        $window = Get-KcWeeklyWindow -Now $Now -Schedule @(Get-KcProp $State 'schedule' @()) -TimeZone $TimeZone
    }
    if ($null -ne $window) {
        $d.Locked = $true; $d.Reason = 'schedule'; $d.UnlockAt = $window.End
        $d.EpisodeKey = 'schedule:' + (ConvertTo-KcIso $window.Start)
    }
    $d
}

function Update-KcLockEpisode {
    <#
        Track the countdown for the current lock episode in $Local (persisted by the caller).
        Schedule locks get the configured countdown; immediate locks apply at once. Reusing the stored
        deadline for the same episode means rebooting or killing the agent can't restart the countdown.
        Returns the UI mode: 'none' | 'countdown' | 'locked', plus the deadline.
    #>
    param([Parameter(Mandatory)][hashtable]$Decision, [Parameter(Mandatory)]$Local, [DateTimeOffset]$Now, [int]$CountdownSeconds)
    if (-not $Decision.Locked) {
        $Local.lock_episode = $null
        return @{ Mode = 'none'; CountdownEndsAt = $null }
    }
    $episode = $Local.lock_episode
    if ($null -eq $episode -or (Get-KcProp $episode 'key') -ne $Decision.EpisodeKey) {
        $ends = $Now
        if ($Decision.Reason -eq 'schedule') { $ends = $Now.AddSeconds([Math]::Max(0, $CountdownSeconds)) }
        $episode = [pscustomobject]@{ key = $Decision.EpisodeKey; countdown_ends_at = (ConvertTo-KcIso $ends) }
        $Local.lock_episode = $episode
    }
    $deadline = ConvertTo-KcDate (Get-KcProp $episode 'countdown_ends_at')
    if ($null -ne $deadline -and $Now -lt $deadline) { return @{ Mode = 'countdown'; CountdownEndsAt = $deadline } }
    @{ Mode = 'locked'; CountdownEndsAt = $deadline }
}

# --- PIN ---------------------------------------------------------------------------------------------------

function Test-KcBytesEqual {
    param([byte[]]$A, [byte[]]$B)
    if ($A.Length -ne $B.Length) { return $false }
    $diff = 0
    for ($i = 0; $i -lt $A.Length; $i++) { $diff = $diff -bor ($A[$i] -bxor $B[$i]) }
    $diff -eq 0
}

function Test-KcPin {
    <# PBKDF2-HMAC-SHA256, matching server/app/security.py hash_pin. #>
    param([Parameter(Mandatory)][string]$Pin, [Parameter(Mandatory)]$PinConfig)
    $hashB64 = Get-KcProp $PinConfig 'hash'
    $saltB64 = Get-KcProp $PinConfig 'salt'
    $iterations = [int](Get-KcProp $PinConfig 'iterations' 0)
    if (-not $hashB64 -or -not $saltB64 -or $iterations -lt 1) { return $false }
    $expected = [Convert]::FromBase64String($hashB64)
    $kdf = New-Object Security.Cryptography.Rfc2898DeriveBytes($Pin, [Convert]::FromBase64String($saltB64), $iterations, [Security.Cryptography.HashAlgorithmName]::SHA256)
    try { Test-KcBytesEqual -A $kdf.GetBytes(32) -B $expected } finally { $kdf.Dispose() }
}

function Invoke-KcPinAttempt {
    <#
        Verify a PIN typed at the lock screen and apply lockout / override rules to $Local.
        Returns @{Ok; Message; Events} where Events are ready for Add-KcEvent.
    #>
    param(
        [Parameter(Mandatory)][string]$Pin,
        $State,
        [Parameter(Mandatory)]$Local,
        [Parameter(Mandatory)][DateTimeOffset]$Now,
        [hashtable]$Decision
    )
    $events = New-Object System.Collections.ArrayList
    $pinCfg = Get-KcProp $State 'pin'
    $lockedUntil = ConvertTo-KcDate $Local.pin_locked_until
    if ($null -ne $lockedUntil -and $Now -lt $lockedUntil) {
        [void]$events.Add(@{ type = 'pin.rejected_locked_out'; severity = 'warning'; message = 'PIN entry attempted during lockout' })
        return @{ Ok = $false; Message = "Too many wrong attempts. Try again after $($lockedUntil.ToLocalTime().ToString('HH:mm'))."; Events = $events }
    }
    if ($null -eq (Get-KcProp $pinCfg 'hash')) {
        return @{ Ok = $false; Message = 'No PIN has been set for this PC.'; Events = $events }
    }
    if ($Pin -notmatch '^\d{4,10}$' -or -not (Test-KcPin -Pin $Pin -PinConfig $pinCfg)) {
        $Local.pin_failures = [int]$Local.pin_failures + 1
        [void]$events.Add(@{ type = 'pin.failed'; severity = 'warning'; message = "Wrong PIN entered (attempt $($Local.pin_failures))" })
        $max = [int](Get-KcProp $pinCfg 'max_attempts' 5)
        if ($Local.pin_failures -ge $max) {
            $until = $Now.AddMinutes([int](Get-KcProp $pinCfg 'lockout_minutes' 15))
            $Local.pin_locked_until = ConvertTo-KcIso $until
            $Local.pin_failures = 0
            [void]$events.Add(@{ type = 'pin.lockout'; severity = 'warning'; message = "PIN entry locked until $(ConvertTo-KcIso $until)" })
            return @{ Ok = $false; Message = "Too many wrong attempts. Try again after $($until.ToLocalTime().ToString('HH:mm'))."; Events = $events }
        }
        return @{ Ok = $false; Message = 'Wrong PIN.'; Events = $events }
    }

    $Local.pin_failures = 0
    $Local.pin_locked_until = $null
    if ($null -ne $Decision -and $Decision.Reason -eq 'immediate' -and $Decision.LockRequestId) {
        $Local.released_locks = Add-KcBoundedItem -List $Local.released_locks -Item $Decision.LockRequestId
        [void]$events.Add(@{ type = 'lock.released_by_pin'; message = 'Immediate lock released with the PIN'; data = @{ lock_request_id = $Decision.LockRequestId } })
    }
    $minutes = [int](Get-KcProp $pinCfg 'override_minutes' 60)
    $grant = [pscustomobject]@{ starts_at = (ConvertTo-KcIso $Now); ends_at = (ConvertTo-KcIso $Now.AddMinutes($minutes)) }
    $Local.local_overrides = Add-KcBoundedItem -List @($Local.local_overrides | Where-Object { (ConvertTo-KcDate $_.ends_at) -gt $Now }) -Item $grant -Max 20
    [void]$events.Add(@{ type = 'pin.override_granted'; message = "Unlocked with PIN for $minutes minutes"; data = @{ starts_at = $grant.starts_at; ends_at = $grant.ends_at } })
    @{ Ok = $true; Message = "Unlocked for $minutes minutes."; Events = $events }
}

# --- Logging & event queue -------------------------------------------------------------------------------

function Write-KcLog {
    <# Local log, one file per month, 12 months kept. #>
    param([Parameter(Mandatory)][string]$Message, [ValidateSet('info', 'warning', 'critical', 'error')][string]$Level = 'info')
    $p = Get-KcPaths
    try {
        if (-not [IO.Directory]::Exists($p.Logs)) { [void][IO.Directory]::CreateDirectory($p.Logs) }
        $now = Get-KcNow
        $file = Join-Path $p.Logs ('agent-{0}.log' -f $now.ToString('yyyy-MM', [Globalization.CultureInfo]::InvariantCulture))
        $isNew = -not [IO.File]::Exists($file)
        [IO.File]::AppendAllText($file, ('{0} [{1}] {2}{3}' -f (ConvertTo-KcIso $now), $Level.ToUpperInvariant(), $Message, [Environment]::NewLine), $script:Utf8)
        if ($isNew) {
            Get-ChildItem -Path $p.Logs -Filter 'agent-*.log' | Sort-Object Name -Descending | Select-Object -Skip 12 | Remove-Item -Force -ErrorAction SilentlyContinue
        }
    } catch { }
}

$script:MaxQueuedEvents = 5000

function Add-KcEvent {
    <# Queue an event for upload (and mirror it to the local log). Survives restarts and offline periods. #>
    param(
        [Parameter(Mandatory)][string]$Type,
        [string]$Message,
        [ValidateSet('info', 'warning', 'critical')][string]$Severity = 'info',
        [hashtable]$Data
    )
    $ev = [ordered]@{
        client_event_id = [guid]::NewGuid().ToString()
        event_type      = $Type
        severity        = $Severity
        message         = $Message
        data            = $Data
        occurred_at     = ConvertTo-KcIso (Get-KcNow)
    }
    $level = $Severity
    Write-KcLog -Level $level -Message ("{0}: {1}" -f $Type, $Message)
    $p = Get-KcPaths
    $line = (ConvertTo-Json -InputObject $ev -Depth 8 -Compress) + "`n"
    [IO.File]::AppendAllText($p.QueueFile, $line, $script:Utf8)
}

function Add-KcEventFromResult {
    param([Parameter(Mandatory)][hashtable]$Event)
    $sev = 'info'
    if ($Event.ContainsKey('severity')) { $sev = $Event.severity }
    $data = $null
    if ($Event.ContainsKey('data')) { $data = $Event.data }
    Add-KcEvent -Type $Event.type -Message $Event.message -Severity $sev -Data $data
}

function Get-KcQueuedEvents {
    $p = Get-KcPaths
    if (-not [IO.File]::Exists($p.QueueFile)) { return , @() }
    $lines = [IO.File]::ReadAllLines($p.QueueFile, $script:Utf8) | Where-Object { $_.Trim() }
    , @($lines)
}

function Set-KcQueuedEvents {
    param([string[]]$Lines)
    $p = Get-KcPaths
    $Lines = @($Lines)
    if ($Lines.Count -gt $script:MaxQueuedEvents) {
        # Offline for a very long time: keep the newest events.
        $Lines = $Lines[($Lines.Count - $script:MaxQueuedEvents)..($Lines.Count - 1)]
    }
    $tmp = "$($p.QueueFile).tmp"
    $text = ''
    if ($Lines.Count) { $text = ($Lines -join "`n") + "`n" }
    [IO.File]::WriteAllText($tmp, $text, $script:Utf8)
    Move-KcFileOver -Source $tmp -Destination $p.QueueFile
}

# --- Device token (DPAPI, machine scope; the config folder ACL is the real boundary) ------------------------

function Protect-KcSecret {
    param([Parameter(Mandatory)][string]$Secret)
    $bytes = $script:Utf8.GetBytes($Secret)
    if (Test-KcIsWindows) {
        Add-Type -AssemblyName System.Security
        $bytes = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
    }
    [Convert]::ToBase64String($bytes)
}

function Unprotect-KcSecret {
    param([Parameter(Mandatory)][string]$Protected)
    $bytes = [Convert]::FromBase64String($Protected)
    if (Test-KcIsWindows) {
        Add-Type -AssemblyName System.Security
        $bytes = [Security.Cryptography.ProtectedData]::Unprotect($bytes, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
    }
    $script:Utf8.GetString($bytes)
}

function Save-KcDeviceToken {
    param([Parameter(Mandatory)][string]$Token)
    [IO.File]::WriteAllText((Get-KcPaths).TokenFile, (Protect-KcSecret $Token), $script:Utf8)
}

function Get-KcDeviceToken {
    $f = (Get-KcPaths).TokenFile
    if (-not [IO.File]::Exists($f)) { return $null }
    Unprotect-KcSecret ([IO.File]::ReadAllText($f, $script:Utf8).Trim())
}

# --- HTTP ---------------------------------------------------------------------------------------------------

function Get-KcHttpClient {
    if ($null -eq $script:Http) {
        Add-Type -AssemblyName System.Net.Http
        # Windows PowerShell 5.1 defaults can exclude TLS 1.2.
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $script:Http = New-Object System.Net.Http.HttpClient
        # Short: the watchdog loop waits on each call, and must get back to supervising the UI agent quickly.
        $script:Http.Timeout = [TimeSpan]::FromSeconds(10)
        $script:Http.DefaultRequestHeaders.UserAgent.ParseAdd("KidputerControlAgent/$script:AgentVersion")
    }
    $script:Http
}

function Invoke-KcApi {
    <#
        Call the cloud API. Returns @{Status; Body (parsed JSON or $null); Headers (hashtable, lower-case keys)}.
        Throws only on network failure; HTTP errors come back as a Status.
    #>
    param(
        [Parameter(Mandatory)][string]$BaseUrl,
        [Parameter(Mandatory)][ValidateSet('GET', 'POST')][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        $Body,
        [string]$Token,
        [hashtable]$Headers = @{}
    )
    $client = Get-KcHttpClient
    $req = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::new($Method), ($BaseUrl.TrimEnd('/') + $Path))
    if ($Token) { $req.Headers.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Bearer', $Token) }
    foreach ($k in $Headers.Keys) { [void]$req.Headers.TryAddWithoutValidation($k, [string]$Headers[$k]) }
    if ($null -ne $Body) {
        $json = $Body
        if ($Body -isnot [string]) { $json = ConvertTo-Json -InputObject $Body -Depth 12 -Compress }
        $req.Content = New-Object System.Net.Http.StringContent($json, $script:Utf8, 'application/json')
    }
    try {
        $resp = $client.SendAsync($req).GetAwaiter().GetResult()
        $text = $resp.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        $hdrs = @{}
        foreach ($h in $resp.Headers) { $hdrs[$h.Key.ToLowerInvariant()] = ($h.Value -join ',') }
        $parsed = $null
        if ($text -and $resp.Content.Headers.ContentType -and $resp.Content.Headers.ContentType.MediaType -eq 'application/json') {
            $parsed = $text | ConvertFrom-Json
        }
        @{ Status = [int]$resp.StatusCode; Body = $parsed; Headers = $hdrs; Raw = $text }
    } finally {
        $req.Dispose()
    }
}

function Invoke-KcEnroll {
    param([Parameter(Mandatory)][string]$BaseUrl, [Parameter(Mandatory)][string]$EnrollmentCode)
    $r = Invoke-KcApi -BaseUrl $BaseUrl -Method POST -Path '/api/v1/agent/enroll' -Body @{
        enrollment_code = $EnrollmentCode
        hostname        = [Environment]::MachineName
        agent_version   = $script:AgentVersion
    }
    if ($r.Status -ne 200) { throw "Enrollment failed (HTTP $($r.Status)): $($r.Raw)" }
    $r.Body
}

function Sync-KcState {
    <#
        Pull the desired-state snapshot. Returns @{Changed; Offline; State; ServerTime; Unauthorized}.
        On 200 the snapshot is written to the local cache, which is what enforcement reads.
    #>
    param([Parameter(Mandatory)][string]$BaseUrl, [Parameter(Mandatory)][string]$Token, $CachedState)
    $headers = @{}
    $etag = Get-KcProp $CachedState 'etag'
    if ($etag) { $headers['If-None-Match'] = $etag }
    try {
        $r = Invoke-KcApi -BaseUrl $BaseUrl -Method GET -Path '/api/v1/agent/state' -Token $Token -Headers $headers
    } catch {
        return @{ Changed = $false; Offline = $true; State = $CachedState; Error = $_.Exception.Message }
    }
    $serverTime = $null
    if ($r.Headers.ContainsKey('x-server-time')) { $serverTime = ConvertTo-KcDate $r.Headers['x-server-time'] }
    switch ($r.Status) {
        200 {
            Write-KcJsonFile -Path (Get-KcPaths).StateFile -Object $r.Body
            return @{ Changed = $true; Offline = $false; State = $r.Body; ServerTime = $serverTime }
        }
        304 { return @{ Changed = $false; Offline = $false; State = $CachedState; ServerTime = $serverTime } }
        401 { return @{ Changed = $false; Offline = $false; State = $CachedState; Unauthorized = $true } }
        default { return @{ Changed = $false; Offline = $true; State = $CachedState; Error = "HTTP $($r.Status)" } }
    }
}

function Send-KcQueuedEvents {
    <# Upload queued events in batches. Returns the number sent; stops quietly at the first failure. #>
    param([Parameter(Mandatory)][string]$BaseUrl, [Parameter(Mandatory)][string]$Token, [int]$BatchSize = 500, [int]$MaxBatches = 1000)
    $sent = 0
    for ($n = 0; $n -lt $MaxBatches; $n++) {
        $lines = Get-KcQueuedEvents
        if ($lines.Count -eq 0) { break }
        $batch = @($lines | Select-Object -First $BatchSize)
        $events = New-Object System.Collections.ArrayList
        foreach ($l in $batch) { try { [void]$events.Add(($l | ConvertFrom-Json)) } catch { } }  # drop corrupt lines
        if ($events.Count) {
            $body = '{"events":' + (ConvertTo-Json -InputObject @($events) -Depth 10 -Compress) + '}'
            try { $r = Invoke-KcApi -BaseUrl $BaseUrl -Method POST -Path '/api/v1/agent/events' -Token $Token -Body $body } catch { break }
            if ($r.Status -ne 200) { break }
        }
        Set-KcQueuedEvents -Lines @($lines | Select-Object -Skip $batch.Count)
        $sent += $batch.Count
    }
    $sent
}

function Send-KcHeartbeat {
    param([Parameter(Mandatory)][string]$BaseUrl, [Parameter(Mandatory)][string]$Token, [hashtable]$Status)
    try {
        $r = Invoke-KcApi -BaseUrl $BaseUrl -Method POST -Path '/api/v1/agent/heartbeat' -Token $Token -Body @{ agent_version = $script:AgentVersion; status = $Status }
        $r.Status -eq 204
    } catch { $false }
}

# --- Tamper detection ------------------------------------------------------------------------------------------

function Get-KcFileHashes {
    param([Parameter(Mandatory)][string]$InstallRoot)
    $result = [ordered]@{}
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        Get-ChildItem -Path $InstallRoot -Recurse -File -Include '*.ps1', '*.psm1', '*.psd1', '*.cs' | Sort-Object FullName | ForEach-Object {
            $rel = $_.FullName.Substring($InstallRoot.TrimEnd('\', '/').Length + 1)
            $stream = [IO.File]::OpenRead($_.FullName)
            try { $result[$rel] = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') } finally { $stream.Dispose() }
        }
    } finally { $sha.Dispose() }
    $result
}

function Test-KcTamper {
    <# Compare installed files to the manifest written at install. Returns a list of problems (empty = OK). #>
    param([Parameter(Mandatory)][string]$InstallRoot, [string]$ManifestPath = (Get-KcPaths).Manifest)
    $problems = New-Object System.Collections.ArrayList
    $manifest = $null
    try { $manifest = Read-KcJsonFile -Path $ManifestPath } catch { }
    if ($null -eq $manifest) { [void]$problems.Add('manifest missing or unreadable'); return , @($problems) }
    $expected = Get-KcProp $manifest 'files'
    if ($null -eq $expected) { [void]$problems.Add('manifest has no file list'); return , @($problems) }
    $actual = Get-KcFileHashes -InstallRoot $InstallRoot
    foreach ($p in $expected.PSObject.Properties) {
        if (-not $actual.Contains($p.Name)) { [void]$problems.Add("missing: $($p.Name)") }
        elseif ($actual[$p.Name] -ne $p.Value) { [void]$problems.Add("modified: $($p.Name)") }
    }
    foreach ($name in $actual.Keys) {
        if ($null -eq $expected.PSObject.Properties[$name]) { [void]$problems.Add("unexpected: $name") }
    }
    , @($problems)
}

# --- IPC between watchdog (SYSTEM) and UI agent (child's session) ------------------------------------------------

function New-KcInboxRequest {
    <# UI agent -> watchdog. The inbox ACL lets Users create files but not list or read others'. #>
    param([Parameter(Mandatory)][string]$Type, [hashtable]$Fields = @{})
    $req = @{ type = $Type; request_id = [guid]::NewGuid().ToString(); created_at = (ConvertTo-KcIso (Get-KcNow)) }
    foreach ($k in $Fields.Keys) { $req[$k] = $Fields[$k] }
    $path = Join-Path (Get-KcPaths).Inbox ("{0}.json" -f $req.request_id)
    [IO.File]::WriteAllText($path, (ConvertTo-Json -InputObject $req -Compress), $script:Utf8)
    $req.request_id
}

function Get-KcInboxRequests {
    <# Watchdog side: read and delete pending requests, oldest first. Oversized/corrupt files are discarded. #>
    param([int]$MaxBytes = 4096, [int]$MaxRequests = 50)
    $dir = (Get-KcPaths).Inbox
    $out = New-Object System.Collections.ArrayList
    if (-not [IO.Directory]::Exists($dir)) { return , @() }
    $files = Get-ChildItem -Path $dir -File | Sort-Object LastWriteTimeUtc | Select-Object -First $MaxRequests
    foreach ($f in $files) {
        try {
            if ($f.Length -le $MaxBytes -and $f.Extension -eq '.json') {
                $obj = [IO.File]::ReadAllText($f.FullName, $script:Utf8) | ConvertFrom-Json
                if ($null -ne $obj -and (Get-KcProp $obj 'type')) { [void]$out.Add($obj) }
            }
        } catch { } finally {
            Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue
        }
    }
    , @($out)
}

# --- Installer helpers ------------------------------------------------------------------------------------------

function Resolve-KcInstallSettings {
    <#
        Merge install-config.json (written into the dashboard's one-click download) with explicit
        Install.ps1 parameters. Explicit parameters win. Returns @{ApiUrl; EnrollmentCode; ChildUser;
        AllowInsecureHttp; ExpiresAt; FromConfig}.
    #>
    param($Config, [string]$ApiUrl, [string]$EnrollmentCode, [string]$ChildUser, [switch]$AllowInsecureHttp)
    $r = @{
        ApiUrl            = $ApiUrl
        EnrollmentCode    = $EnrollmentCode
        ChildUser         = $ChildUser
        AllowInsecureHttp = [bool]$AllowInsecureHttp
        ExpiresAt         = $null
        FromConfig        = $false
    }
    if ($null -eq $Config) { return $r }
    $r.FromConfig = $true
    if (-not $r.ApiUrl) { $r.ApiUrl = [string](Get-KcProp $Config 'api_url' '') }
    if (-not $r.EnrollmentCode) {
        $r.EnrollmentCode = [string](Get-KcProp $Config 'enrollment_code' '')
        $r.ExpiresAt = ConvertTo-KcDate (Get-KcProp $Config 'expires_at')
    }
    if (-not $r.ChildUser) { $r.ChildUser = [string](Get-KcProp $Config 'child_user' '') }
    if (-not $r.AllowInsecureHttp) { $r.AllowInsecureHttp = [bool](Get-KcProp $Config 'allow_insecure_http' $false) }
    $r
}

function Select-KcChildCandidates {
    <#
        Windows accounts worth offering as "the child": enabled, not built-in (Administrator, Guest,
        DefaultAccount, WDAGUtilityAccount), not an administrator, and not the parent running the installer.
        $Users: objects with Name, FullName, Enabled and SID (as from Get-LocalUser).
    #>
    param([object[]]$Users, [string[]]$AdminSids = @(), [string]$CurrentSid)
    $builtinRids = @('500', '501', '503', '504')
    , @($Users | Where-Object {
            $sid = [string]$_.SID
            $_.Enabled -and $sid -and $sid -ne $CurrentSid -and ($AdminSids -notcontains $sid) -and
            ($builtinRids -notcontains ($sid -split '-')[-1])
        } | Sort-Object Name)
}

Export-ModuleMember -Function *-Kc*
