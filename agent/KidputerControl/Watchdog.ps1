#Requires -Version 5.1
<#
.SYNOPSIS
    Kidputer Control SYSTEM watchdog. Owns enforcement, tamper detection and cloud sync.

.DESCRIPTION
    Runs as SYSTEM from the "\KidputerControl\Watchdog" scheduled task (at startup, re-triggered every
    minute as a backstop; a mutex keeps it single-instance). A standard user can't stop or modify it.

    Each ~1s tick:
      1. Handle requests from the UI agent (PIN attempts, message acks) via the inbox folder.
      2. Advance the cloud sync by at most one HTTP call (state -> events -> heartbeat), so a slow
         network never delays UI supervision by more than the HTTP timeout.
      3. Decide lock/unlock from the *cached* state (works offline) and publish ipc\ui-state.json.
      4. Make sure the UI agent is running in the child's console session; relaunch it if it died.
      5. Every minute, verify installed files against the install-time manifest.
#>
param(
    [string]$InstallRoot = $PSScriptRoot,
    [string]$DataRoot = (Join-Path $env:ProgramData 'KidputerControl')
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $InstallRoot 'Modules\KidputerControl.Common.psm1') -Force
Import-Module (Join-Path $InstallRoot 'Modules\KidputerControl.Apps.psm1') -Force
Import-Module (Join-Path $InstallRoot 'Modules\KidputerControl.Update.psm1') -Force
Import-Module (Join-Path $InstallRoot 'Modules\KidputerControl.Monitor.psm1') -Force
Import-Module (Join-Path $InstallRoot 'Modules\KidputerControl.Files.psm1') -Force
$paths = Initialize-KcPaths -DataRoot $DataRoot

# --- Single instance ------------------------------------------------------------------------------------
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, 'Global\KidputerControlWatchdog', [ref]$createdNew)
if (-not $createdNew) {
    try {
        # A previous instance that died without releasing leaves the mutex abandoned: take it over.
        if (-not $mutex.WaitOne(0)) { exit 0 }
    } catch [System.Threading.AbandonedMutexException] { }
}

Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $InstallRoot 'Modules\KidputerControl.Native.cs')))
try { [KcShutdownWatcher]::Start() } catch { Write-KcLog -Level warning -Message "Shutdown watcher unavailable: $($_.Exception.Message)" }

$agentCfg = Read-KcJsonFile -Path $paths.AgentFile
if ($null -eq $agentCfg) { Write-KcLog -Level error -Message 'agent.json missing; reinstall required'; exit 1 }
$apiUrl = [string]$agentCfg.api_url
$childSid = [string]$agentCfg.child_user_sid
$token = $null
try { $token = Get-KcDeviceToken } catch { Write-KcLog -Level error -Message "Cannot read device token: $($_.Exception.Message)" }

$state = $null
try { $state = Read-KcJsonFile -Path $paths.StateFile } catch { Write-KcLog -Level warning -Message 'Cached state unreadable; waiting for sync' }
$local = Read-KcLocalState

$uiScript = Join-Path $InstallRoot 'UiAgent.ps1'
$powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$uiCmdLine = '"{0}" -NoProfile -NonInteractive -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "{1}" -DataRoot "{2}"' -f $powershellExe, $uiScript, $DataRoot

# --- Runtime state ---------------------------------------------------------------------------------------
$ui = @{ Pid = 0; StartTime = $null; SessionId = [KcNative]::NoSession; Launches = New-Object System.Collections.Queue; EverStarted = $false }
$sync = @{ Phase = 'state'; NextAt = [DateTimeOffset]::MinValue; Failures = 0; LastOkAt = $null; Offline = $true; Unauthorized = $false; SkewReportedAt = [DateTimeOffset]::MinValue; SkewSeconds = $null }
$tamper = @{ NextAt = [DateTimeOffset]::MinValue; Problems = @() }
$adminCheck = @{ NextAt = [DateTimeOffset]::MinValue; IsAdmin = $false }
$power = @{ LastTick = $null; ShutdownReported = $false }
$reports = @{ ProcessesAt = [DateTimeOffset]::MinValue }
$update = @{ LastAttempt = [DateTimeOffset]::MinValue; Bundle = $null }
try { $update.Bundle = (Get-KcLocalBundle -Root $InstallRoot).Fingerprint } catch { Write-KcLog -Level warning -Message "Can't fingerprint the installed files: $($_.Exception.Message)" }
$appStops = New-Object System.Collections.ArrayList   # apps being closed: warning shown, then closed
$watchdogStartTicks = [DateTime]::UtcNow.Ticks
$appRun = @{ Identity = @{}; Cmdline = @{}; State = @{}; BlockedEventAt = @{}; Notices = (New-Object System.Collections.ArrayList); LastUsageMsg = [DateTimeOffset]::MinValue; UsageSavedAt = [DateTimeOffset]::MinValue; UsageDirty = $false; UsageSentAt = [DateTimeOffset]::MinValue }
$usageFile = Join-Path $paths.Config 'usage.json'
$usage = @{}
try {
    $u = Read-KcJsonFile -Path $usageFile
    if ($u) {
        foreach ($d in $u.PSObject.Properties) {
            $usage[$d.Name] = @{}
            foreach ($k in $d.Value.PSObject.Properties) {
                $usage[$d.Name][$k.Name] = @{ name = [string]$k.Value.name; category = [string](Get-KcProp $k.Value 'category' ''); seconds = [int]$k.Value.seconds; path = [string](Get-KcProp $k.Value 'path' '') }
            }
        }
    }
} catch { $usage = @{} }
$script:childSession = $null
$lastMode = $null
$lastUiJson = $null
$pinResult = $null
$consoleIsChild = $false
$localDirty = $false

function Save-Local {
    Write-KcJsonFile -Path $paths.LocalFile -Object $local
}

function Test-Live {
    <# A parent has a live view open, or a command is waiting: check in every few seconds. #>
    $until = ConvertTo-KcDate (Get-KcProp $state 'live_until')
    ($null -ne $until -and (Get-KcNow) -lt $until) -or @(Get-KcProp $state 'commands' @()).Count -gt 0 -or $appStops.Count -gt 0
}

function Get-PollSeconds {
    if (Test-Live) { return 3 }
    $s = [int](Get-KcProp $state 'poll_interval_seconds' 30)
    [Math]::Min([Math]::Max($s, 5), 3600)
}

function Test-ChildIsAdmin {
    try {
        $admins = Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop
        return [bool]($admins | Where-Object { $_.SID -and $_.SID.Value -eq $childSid })
    } catch { return $false }
}

function Update-ChildAdminStatus {
    <# Re-checked every 10 minutes; the dashboard compares it with the PC's mode (Managed or Trusted). #>
    $now = Get-KcNow
    if ($now -lt $adminCheck.NextAt) { return }
    $adminCheck.NextAt = $now.AddMinutes(10)
    $adminCheck.IsAdmin = Test-ChildIsAdmin
}

# --- UI agent supervision -----------------------------------------------------------------------------------

function Test-UiAlive {
    if ($ui.Pid -le 0) { return $false }
    $p = Get-Process -Id $ui.Pid -ErrorAction SilentlyContinue
    # Compare start time too, so a recycled PID isn't mistaken for our process.
    ($null -ne $p) -and ($p.StartTime -eq $ui.StartTime)
}

function Stop-StrayUiAgents {
    <# Kill UI agents we don't track (e.g. left over from a previous watchdog instance). #>
    param([int]$KeepPid = 0)
    try {
        Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -and $_.CommandLine.Contains($uiScript) -and $_.ProcessId -ne $KeepPid } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    } catch { }
}

function Update-UiAgent {
    $session = [KcNative]::ActiveConsoleSessionId()
    $sid = $null
    if ($session -ne [KcNative]::NoSession) { $sid = [KcNative]::GetSessionUserSid($session) }
    $script:consoleIsChild = ($null -ne $sid) -and ($sid -eq $childSid)
    $script:childSession = $null
    if (-not $script:consoleIsChild) { return }   # enforcement applies to the child's account only
    $script:childSession = [int]$session

    if ((Test-UiAlive) -and $ui.SessionId -eq $session) { return }

    $now = Get-KcNow
    while ($ui.Launches.Count -and ($now - $ui.Launches.Peek()).TotalSeconds -gt 60) { [void]$ui.Launches.Dequeue() }
    if ($ui.Launches.Count -ge 20) { return }   # crash loop: back off, keep retrying next minute

    if ($ui.EverStarted -and $ui.Pid -gt 0) {
        $why = 'exited'
        if ($ui.SessionId -ne $session) { $why = 'session changed' }
        Add-KcEvent -Type 'ui_agent.restarted' -Severity warning -Message "UI agent $why; relaunching" -Data @{ previous_pid = $ui.Pid }
    }
    Stop-StrayUiAgents
    try {
        $newPid = [KcNative]::LaunchInSession($session, $powershellExe, $uiCmdLine, $InstallRoot)
        $proc = Get-Process -Id $newPid -ErrorAction SilentlyContinue
        $ui.Pid = $newPid
        $ui.StartTime = $null
        if ($proc) { $ui.StartTime = $proc.StartTime }
        $ui.SessionId = $session
        $ui.EverStarted = $true
        $ui.Launches.Enqueue($now)
        Write-KcLog -Message "UI agent started (pid $newPid, session $session)"
    } catch {
        $ui.Launches.Enqueue($now)
        Write-KcLog -Level error -Message "UI agent launch failed: $($_.Exception.Message)"
    }
}

# --- Inbox (requests from the UI agent; untrusted: they come from the child's account) ------------------------------

function Get-ActiveMessage {
    $m = Get-KcProp $state 'message'
    if ($null -eq $m) { return $null }
    if (@($local.dismissed_messages) -contains [string]$m.id) { return $null }
    $m
}

function Invoke-Inbox {
    param([hashtable]$Decision)
    $handled = $false
    foreach ($req in (Get-KcInboxRequests)) {
        $type = [string](Get-KcProp $req 'type')
        $requestId = [string](Get-KcProp $req 'request_id' '')
        if ($requestId.Length -gt 64) { continue }
        switch ($type) {
            'pin_attempt' {
                $pin = [string](Get-KcProp $req 'pin' '')
                if ($pin.Length -gt 32) { $pin = '' }
                $r = Invoke-KcPinAttempt -Pin $pin -State $state -Local $local -Now (Get-KcNow) -Decision $Decision
                foreach ($e in $r.Events) { Add-KcEventFromResult -Event $e }
                $script:pinResult = @{ request_id = $requestId; ok = $r.Ok; message = $r.Message }
                $script:localDirty = $true
                $handled = $true
            }
            'message_displayed' {
                $current = Get-ActiveMessage
                $id = [string](Get-KcProp $req 'message_id' '')
                if ($current -and $id -eq [string]$current.id -and @($local.displayed_messages) -notcontains $id) {
                    $local.displayed_messages = Add-KcBoundedItem -List $local.displayed_messages -Item $id
                    Add-KcEvent -Type 'message.displayed' -Message 'Message shown on screen' -Data @{ message_id = $id }
                    $script:localDirty = $true
                    $handled = $true
                }
            }
            'message_closed' {
                $id = [string](Get-KcProp $req 'message_id' '')
                $reason = [string](Get-KcProp $req 'reason' '')
                $current = Get-ActiveMessage
                if ($reason -eq 'user' -and $current -and $id -eq [string]$current.id) {
                    if ($current.mode -eq 'closable') {
                        $local.dismissed_messages = Add-KcBoundedItem -List $local.dismissed_messages -Item $id
                        Add-KcEvent -Type 'message.dismissed' -Message 'Message closed by the child' -Data @{ message_id = $id }
                        $script:localDirty = $true
                    } else {
                        Add-KcEvent -Type 'message.close_blocked' -Severity warning -Message 'Attempt to close a persistent message' -Data @{ message_id = $id }
                    }
                    $handled = $true
                }
            }
            'usage' { Add-UsageSample -Request $req }
            'ui_error' {
                $msg = [string](Get-KcProp $req 'message' '')
                if ($msg.Length -gt 500) { $msg = $msg.Substring(0, 500) }
                Add-KcEvent -Type 'ui_agent.error' -Severity warning -Message $msg
                $handled = $true
            }
        }
    }
    if ($handled) {
        # Get acknowledgements (and PIN overrides) to the dashboard promptly.
        $sync.Phase = 'events'
        $sync.NextAt = (Get-KcNow).AddSeconds(2)
    }
}

# --- Cloud sync: one HTTP call per tick ------------------------------------------------------------------------------

function Invoke-SyncStep {
    $now = Get-KcNow
    if ($now -lt $sync.NextAt -or -not $token -or -not $apiUrl) { return }
    switch ($sync.Phase) {
        'state' {
            $r = Sync-KcState -BaseUrl $apiUrl -Token $token -CachedState $state
            if ($r.Unauthorized) {
                if (-not $sync.Unauthorized) { Add-KcEvent -Type 'agent.unauthorized' -Severity critical -Message 'Device token rejected; enforcing from cache. Re-enroll this PC.' }
                $sync.Unauthorized = $true
            } else { $sync.Unauthorized = $false }
            if ($r.Offline) {
                if (-not $sync.Offline) { Write-KcLog -Level warning -Message "Cloud unreachable: $($r.Error)" }
                $sync.Offline = $true
                $sync.Failures++
                $backoff = [Math]::Min((Get-PollSeconds) * [Math]::Pow(2, [Math]::Min($sync.Failures, 6)), 300)
                $sync.NextAt = $now.AddSeconds($backoff)
                return
            }
            if ($sync.Offline -and $sync.LastOkAt) { Add-KcEvent -Type 'agent.reconnected' -Message 'Cloud connection restored' }
            $sync.Offline = $false
            $sync.Failures = 0
            $sync.LastOkAt = $now
            if ($r.Changed) { $script:state = $r.State; Write-KcLog -Message 'New configuration received' }
            if ($script:state) { Invoke-AgentUpdate }
            if ($r.ServerTime) {
                $sync.SkewSeconds = [int]($now - $r.ServerTime).TotalSeconds
                if ([Math]::Abs($sync.SkewSeconds) -gt 300 -and ($now - $sync.SkewReportedAt).TotalHours -ge 6) {
                    Add-KcEvent -Type 'clock.skew' -Severity warning -Message "PC clock differs from server by $($sync.SkewSeconds)s" -Data @{ skew_seconds = $sync.SkewSeconds }
                    $sync.SkewReportedAt = $now
                }
            }
            $sync.Phase = 'events'
        }
        'events' {
            [void](Send-KcQueuedEvents -BaseUrl $apiUrl -Token $token -MaxBatches 1)
            if ((Get-KcQueuedEvents).Count -eq 0) { $sync.Phase = 'reports' }
        }
        'reports' {
            # At most one upload per tick: a command result, else a due report.
            if (@($local.command_results).Count) {
                $first = @($local.command_results)[0]
                if (Send-KcCommandResult -BaseUrl $apiUrl -Token $token -Result $first) {
                    $local.command_results = @(@($local.command_results) | Select-Object -Skip 1)
                    $script:localDirty = $true
                }
                return
            }
            $sync.Phase = 'heartbeat'
            $every = 600
            if (Test-Live) { $every = 5 }
            if (($now - $reports.ProcessesAt).TotalSeconds -ge $every) {
                $reports.ProcessesAt = $now
                [void](Send-KcReport -BaseUrl $apiUrl -Token $token -Kind 'processes' -Data (Get-ProcessReport))
            }
            $drivesEvery = 600
            if (Test-Live) { $drivesEvery = 60 }
            $driveReport = $null
            if (($now - $files.DrivesAt).TotalSeconds -ge 30) {
                $driveReport = Get-KcDriveReport
                $key = (@($driveReport.drives | ForEach-Object { $_.name }) + @($driveReport.devices)) -join '|'
                if ($key -ne $files.DrivesKey -or ($now - $files.DrivesAt).TotalSeconds -ge $drivesEvery) {
                    if (Send-KcReport -BaseUrl $apiUrl -Token $token -Kind 'drives' -Data $driveReport) {
                        $files.DrivesKey = $key
                        $files.DrivesAt = $now
                    }
                }
            }
            $usageEvery = 300
            if (Test-Live) { $usageEvery = 30 }
            if ($appRun.UsageDirty -and ($now - $appRun.UsageSentAt).TotalSeconds -ge $usageEvery) {
                if (Send-KcReport -BaseUrl $apiUrl -Token $token -Kind 'usage' -Data @{ days = $usage }) {
                    $appRun.UsageSentAt = $now
                    $appRun.UsageDirty = $false
                }
            }
        }
        'heartbeat' {
            [void](Send-KcHeartbeat -BaseUrl $apiUrl -Token $token -Status (Get-Status))
            $sync.Phase = 'state'
            $sync.NextAt = $now.AddSeconds((Get-PollSeconds))
        }
    }
}

$script:currentDecision = $null
$script:currentMode = @{ Mode = 'none'; CountdownEndsAt = $null }

function Get-Status {
    $d = $script:currentDecision
    $s = @{
        mode                  = $script:currentMode.Mode
        reason                = $(if ($d) { $d.Reason } else { $null })
        console_user_is_child = $script:consoleIsChild
        ui_agent_alive        = (Test-UiAlive)
        tamper_problems       = @($tamper.Problems)
        queued_events         = (Get-KcQueuedEvents).Count
        clock_skew_seconds    = $sync.SkewSeconds
        pin_locked_until      = $local.pin_locked_until
        child_is_admin        = $adminCheck.IsAdmin
        agent_bundle          = $update.Bundle
        scan                  = (Get-ScanStatus)
    }
    if ($d -and $d.UnlockAt) { $s.unlock_at = ConvertTo-KcIso $d.UnlockAt }
    if ($d -and $d.OverrideEndsAt) { $s.override_ends_at = ConvertTo-KcIso $d.OverrideEndsAt }
    $s
}

# --- Apps: reports and closing apps from the dashboard ------------------------------------------------------------

function Get-ChildProcesses {
    if ($null -eq $script:childSession) { return , @() }
    Get-KcSessionProcesses -SessionId $script:childSession -IncludeTitles:([bool](Get-KcProp $state 'show_window_titles' $false))
}

function Get-ProcessReport {
    @{ child_signed_in = ($null -ne $script:childSession); processes = (ConvertTo-KcProcessReport -Processes (Get-ChildProcesses)) }
}

function Complete-Command {
    param([string]$Id, [string]$Status, [hashtable]$Result)
    $local.handled_commands = Add-KcBoundedItem -List $local.handled_commands -Item $Id -Max 200
    $local.command_results = @(@($local.command_results) + @([pscustomobject]@{ id = $Id; status = $Status; result = $Result }))
    $script:localDirty = $true
    $sync.Phase = 'events'
    $sync.NextAt = Get-KcNow
}

function Start-AppStop {
    param($Command)
    $payload = Get-KcProp $Command 'payload'
    $path = [string](Get-KcProp $payload 'path' '')
    $name = [string](Get-KcProp $payload 'name' 'the app')
    if ($name.Length -gt 100) { $name = $name.Substring(0, 100) }
    $grace = [Math]::Min([Math]::Max([int](Get-KcProp $payload 'grace_seconds' 60), 0), 600)
    $targets = Select-KcProcessesToStop -Processes (Get-ChildProcesses) -Path $path -InstallRoot $InstallRoot
    if ($targets.Count -eq 0) {
        Complete-Command -Id $Command.id -Status 'failed' -Result @{ reason = 'not_running'; message = "$name isn't running on the PC." }
        return
    }
    $now = Get-KcNow
    [void]$appStops.Add(@{ CommandId = [string]$Command.id; Path = $path; Name = $name; EndsAt = $now.AddSeconds($grace); ForceAt = $null })
    Add-KcEvent -Type 'app.stop_warning' -Message "Closing $name in $grace seconds" -Data @{ path = $path; grace_seconds = $grace }
}

function Update-AppStops {
    <#
        Warning over: the UI agent (in the child's session, where it can reach the app's windows) asks
        the app to close so it can save; whatever is still running 5 seconds later is force-closed.
    #>
    $now = Get-KcNow
    foreach ($stop in @($appStops)) {
        if ($now -lt $stop.EndsAt) { continue }
        $targets = Select-KcProcessesToStop -Processes (Get-ChildProcesses) -Path $stop.Path -InstallRoot $InstallRoot
        if ($null -eq $stop.ForceAt) {
            $stop.ForceAt = $now.AddSeconds(5)
            $stop.Count = $targets.Count
            continue
        }
        if ($now -lt $stop.ForceAt -and $targets.Count) { continue }
        $forced = 0
        foreach ($t in $targets) { try { Stop-Process -Id $t.Id -Force -ErrorAction Stop; $forced++ } catch { } }
        Add-KcEvent -Type 'app.stopped' -Message "$($stop.Name) was closed" -Data @{ path = $stop.Path; forced = $forced }
        if ($stop.CommandId) { Complete-Command -Id $stop.CommandId -Status 'done' -Result @{ closed = [Math]::Max([int]$stop.Count, $forced); forced = $forced } }
        $appStops.Remove($stop)
    }
}

function Invoke-Commands {
    <# Carry out new commands from the dashboard (each once; ids are remembered across restarts). #>
    foreach ($cmd in @(Get-KcProp $state 'commands' @())) {
        $id = [string](Get-KcProp $cmd 'id' '')
        if (-not $id -or @($local.handled_commands) -contains $id) { continue }
        if (@($appStops | Where-Object { $_.CommandId -and $_.CommandId -eq $id }).Count) { continue }
        switch ([string](Get-KcProp $cmd 'kind' '')) {
            'stop_app' { Start-AppStop -Command $cmd }
            'list_folder' { Invoke-ListFolder -Command $cmd }
            'scan_drive' {
                $letter = ([string](Get-KcProp (Get-KcProp $cmd 'payload') 'drive' '')).ToUpperInvariant()
                $err = $null
                if ($letter -notmatch '^[A-Z]$') { $err = 'Not a drive letter.' } else { $err = Start-DriveScan -Letter $letter -CommandId $id }
                if ($err) { Complete-Command -Id $id -Status 'failed' -Result @{ message = $err } }
                else { $local.handled_commands = Add-KcBoundedItem -List $local.handled_commands -Item $id -Max 200; $script:localDirty = $true }
            }
            default { Complete-Command -Id $id -Status 'failed' -Result @{ reason = 'unsupported'; message = 'This PC needs a newer version of Kidputer Control for that.' } }
        }
    }
}

# --- App rules (roadmap #3, #4) and usage time (#5) ------------------------------------------------------------------

function Get-AppIdentity {
    <# Who a running program is, cached per path; Java's command line (for Minecraft) per process. #>
    param([string]$Path, [string]$ProcessKey, [int]$ProcessId)
    $exe = ($Path -split '[\\/]')[-1].ToLowerInvariant()
    $cmd = $null
    if ($exe -eq 'javaw.exe' -or $exe -eq 'java.exe') {
        if (-not $appRun.Cmdline.ContainsKey($ProcessKey)) {
            $c = ''
            try { $c = [string](Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop).CommandLine } catch { }
            $appRun.Cmdline[$ProcessKey] = $c
        }
        $cmd = $appRun.Cmdline[$ProcessKey]
    }
    if (-not $appRun.Identity.ContainsKey($Path)) {
        if ($appRun.Identity.Count -gt 2000) { $appRun.Identity.Clear() }
        $appRun.Identity[$Path] = @{ Version = (Get-KcFileVersionInfo -Path $Path) }
    }
    New-KcAppIdentity -Path $Path -VersionInfo $appRun.Identity[$Path].Version -CommandLine $cmd
}

function Add-ChildNotice {
    param([string]$Text, [int]$Seconds = 8)
    foreach ($n in @($appRun.Notices)) { if ($n.text -eq $Text) { $n.until = (Get-KcNow).AddSeconds($Seconds); return } }
    [void]$appRun.Notices.Add(@{ text = $Text; until = (Get-KcNow).AddSeconds($Seconds) })
}

function Invoke-AppRules {
    <#
        Apps outside their allowed times: a newly started one is closed at once (it has nothing to save),
        with a message saying when it's available; one that was already open when its time ran out gets
        the same countdown warning as a parent's Close.
    #>
    $rules = @(Get-KcProp $state 'app_rules' @())
    if ($rules.Count -eq 0 -or $null -eq $script:childSession) { $appRun.State.Clear(); return }
    $catalog = @(Get-KcProp $state 'app_catalog' @())
    $now = Get-KcNow
    $horizon = ConvertTo-KcDate (Get-KcProp $state 'lock_windows_until')
    $useWeekly = ($null -eq $horizon -or $now -ge $horizon)
    $alive = @{}
    foreach ($p in (Get-KcSessionProcessList -SessionId $script:childSession)) {
        if (-not $p.Path) { continue }
        if (Test-KcProtectedProcess -Name $p.Name -Path $p.Path -InstallRoot $InstallRoot) { continue }
        $key = "$($p.Id)|$($p.StartTime)"
        $alive[$key] = $true
        $app = Find-KcCatalogApp -Identity (Get-AppIdentity -Path $p.Path -ProcessKey $key -ProcessId $p.Id) -Catalog $catalog
        if ($null -eq $app) { continue }
        $rule = Get-KcAppRule -App $app -Rules $rules
        $d = Test-KcAppAllowed -Rule $rule -Now $now -UseWeekly:$useWeekly
        $prev = $appRun.State[$key]
        if ($d.Allowed) { $appRun.State[$key] = 'allowed'; continue }
        if ($prev -eq 'stopping') { continue }
        # Already open before the watchdog started, or allowed until now: warn first.
        $startedEarlier = $false
        $ticks = 0L
        if ([long]::TryParse([string]$p.StartTime, [ref]$ticks)) { $startedEarlier = $ticks -lt $watchdogStartTicks }
        if ($prev -eq 'allowed' -or ($null -eq $prev -and $startedEarlier)) {
            $grace = [Math]::Min([Math]::Max([int](Get-KcProp $state 'countdown_seconds' 300), 30), 300)
            if (-not @($appStops | Where-Object { $_.Path -eq $p.Path }).Count) {
                [void]$appStops.Add(@{ CommandId = $null; Path = $p.Path; Name = [string]$app.name; EndsAt = $now.AddSeconds($grace); ForceAt = $null })
                Add-KcEvent -Type 'app.time_up' -Message "$($app.name): allowed time is over, closing in $grace seconds" -Data @{ app = $app.id; path = $p.Path }
            }
            $appRun.State[$key] = 'stopping'
            continue
        }
        try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch { }
        $appRun.State[$key] = 'blocked'
        Add-ChildNotice -Text (Get-KcBlockedMessage -Name ([string]$app.name) -Decision $d)
        $last = $appRun.BlockedEventAt[[string]$app.id]
        if ($null -eq $last -or ($now - $last).TotalMinutes -ge 10) {
            $appRun.BlockedEventAt[[string]$app.id] = $now
            Add-KcEvent -Type 'app.blocked' -Message "$($app.name) was blocked (not allowed right now)" -Data @{ app = $app.id; name = $app.name; path = $p.Path }
        }
    }
    foreach ($k in @($appRun.State.Keys)) { if (-not $alive.ContainsKey($k)) { $appRun.State.Remove($k); $appRun.Cmdline.Remove($k) } }
    foreach ($n in @($appRun.Notices)) { if ($now -ge $n.until) { $appRun.Notices.Remove($n) } }
}

function Add-UsageSample {
    <# The UI agent's minute of "what was in front" (untrusted input: validated and capped). #>
    param($Request)
    $now = Get-KcNow
    if (($now - $appRun.LastUsageMsg).TotalSeconds -lt 45) { return }
    $appRun.LastUsageMsg = $now
    $day = Get-KcUsageDay -Now $now -DayStarts @(Get-KcProp $state 'day_starts' @())
    if (-not $day) { $day = [DateTime]::Now.ToString('yyyy-MM-dd') }
    $catalog = @(Get-KcProp $state 'app_catalog' @())
    $budget = 70
    $active = [Math]::Min([Math]::Max([int](Get-KcProp $Request 'active_seconds' 0), 0), 70)
    if ($active -gt 0) { Add-KcUsage -Usage $usage -Day $day -Key '_total' -Name 'Screen time' -Seconds $active }
    foreach ($s in @(Get-KcProp $Request 'samples' @()) | Select-Object -First 10) {
        $path = [string](Get-KcProp $s 'path' '')
        $secs = [Math]::Min([Math]::Max([int](Get-KcProp $s 'seconds' 0), 0), $budget)
        if (-not $path -or $path.Length -gt 1024 -or $secs -le 0) { continue }
        $budget -= $secs
        $pid0 = 0; $pkey = "usage|$path"
        if ($null -ne $script:childSession) {
            $match = @(Get-KcSessionProcessList -SessionId $script:childSession | Where-Object { $_.Path -and [string]::Equals($_.Path, $path, [StringComparison]::OrdinalIgnoreCase) }) | Select-Object -First 1
            if ($match) { $pid0 = $match.Id; $pkey = "$($match.Id)|$($match.StartTime)" }
        }
        $identity = Get-AppIdentity -Path $path -ProcessKey $pkey -ProcessId $pid0
        $app = Find-KcCatalogApp -Identity $identity -Catalog $catalog
        if ($app) {
            Add-KcUsage -Usage $usage -Day $day -Key ([string]$app.id) -Name ([string]$app.name) -Category ([string]$app.category) -Path $path -Seconds $secs
        } else {
            $v = $appRun.Identity[$path].Version
            $name = Get-KcAppDisplayName -Process ([pscustomobject]@{ Name = [IO.Path]::GetFileNameWithoutExtension(($path -split '[\\/]')[-1]) }) -VersionInfo $v
            Add-KcUsage -Usage $usage -Day $day -Key ('exe:' + ($path -split '[\\/]')[-1].ToLowerInvariant()) -Name $name -Path $path -Seconds $secs
        }
        if ($budget -le 0) { break }
    }
    $appRun.UsageDirty = $true
    if (($now - $appRun.UsageSavedAt).TotalSeconds -ge 60) {
        Write-KcJsonFile -Path $usageFile -Object $usage
        $appRun.UsageSavedAt = $now
    }
}

# --- Drives, folder-size scans and the file explorer (roadmap #19) -----------------------------------------------------

$scanDir = Join-Path $paths.Config 'scans'
$files = @{ DrivesAt = [DateTimeOffset]::MinValue; DrivesKey = ''; Scan = $null; CheckAt = [DateTimeOffset]::MinValue }
$lastScansFile = Join-Path $scanDir 'last.json'
$lastScans = @{}
try { $ls = Read-KcJsonFile -Path $lastScansFile; if ($ls) { foreach ($p in $ls.PSObject.Properties) { $lastScans[$p.Name] = [string]$p.Value } } } catch { }

function Start-DriveScan {
    <# Start a folder-size scan of one drive in a separate, background-priority process. #>
    param([string]$Letter, [string]$CommandId)
    if ($files.Scan) { return "A scan of drive $($files.Scan.Letter): is already running." }
    $drive = $null
    try { $drive = [IO.DriveInfo]::new("${Letter}:\") } catch { }
    if ($null -eq $drive -or -not $drive.IsReady) { return "Drive ${Letter}: isn't available on the PC." }
    [void][IO.Directory]::CreateDirectory($scanDir)
    $out = Join-Path $scanDir "$Letter.json"
    $progress = Join-Path $scanDir "$Letter.progress.json"
    foreach ($f in $out, $progress) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
    $min = Get-KcScanMinBytes -UsedBytes ([long]$drive.TotalSize - [long]$drive.AvailableFreeSpace)
    # No quotes round C:\ : a quote right after a backslash would be taken as a literal quote by Windows.
    $scanArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Root {1}:\ -OutFile "{2}" -ProgressFile "{3}" -MinNodeBytes {4}' -f (Join-Path $InstallRoot 'Scan.ps1'), $Letter, $out, $progress, $min
    $proc = Start-Process -FilePath $powershellExe -ArgumentList $scanArgs -WindowStyle Hidden -PassThru
    $files.Scan = @{ Letter = $Letter; Proc = $proc; Out = $out; Progress = $progress; StartedAt = (Get-KcNow); CommandId = $CommandId; Uploaded = $false }
    Add-KcEvent -Type 'files.scan_started' -Message "Scanning drive ${Letter}: for folder sizes"
    $null
}

function Update-DriveScan {
    $s = $files.Scan
    $now = Get-KcNow
    if ($null -eq $s) {
        # Regular scans: when due, and preferably while the child isn't using the PC (or it's locked).
        if ($now -lt $files.CheckAt) { return }
        $files.CheckAt = $now.AddMinutes(10)
        $every = [int](Get-KcProp $state 'scan_every_days' 7)
        $due = Get-KcDueScan -Drives @((Get-KcDriveReport).drives) -LastScans $lastScans -EveryDays $every -Now $now
        if (-not $due) { return }
        $quiet = (-not $script:consoleIsChild) -or ($script:currentMode.Mode -eq 'locked')
        $last = $null
        if ($lastScans.ContainsKey($due)) { $last = ConvertTo-KcDate $lastScans[$due] }
        $veryOverdue = ($null -eq $last) -or (($now - $last).TotalDays -ge 2 * $every)
        if ($quiet -or $veryOverdue) { [void](Start-DriveScan -Letter $due -CommandId $null) }
        return
    }
    if (-not $s.Proc.HasExited) {
        if (($now - $s.StartedAt).TotalHours -ge 2) {
            try { $s.Proc.Kill() } catch { }
            Add-KcEvent -Type 'files.scan_failed' -Severity warning -Message "Scan of drive $($s.Letter): took over 2 hours and was stopped"
            if ($s.CommandId) { Complete-Command -Id $s.CommandId -Status 'failed' -Result @{ message = 'The scan took over 2 hours and was stopped.' } }
            $files.Scan = $null
        }
        return
    }
    if (-not [IO.File]::Exists($s.Out)) {
        Add-KcEvent -Type 'files.scan_failed' -Severity warning -Message "Scan of drive $($s.Letter): failed"
        if ($s.CommandId) { Complete-Command -Id $s.CommandId -Status 'failed' -Result @{ message = 'The scan failed on the PC.' } }
        $files.Scan = $null
        return
    }
    if (-not $token -or -not $apiUrl) { return }
    # Upload the result (retried every tick until the server has it).
    try {
        $body = [IO.File]::ReadAllText($s.Out, (New-Object System.Text.UTF8Encoding($false)))
        $r = Invoke-KcApi -BaseUrl $apiUrl -Method POST -Path "/api/v1/agent/reports/scan:$($s.Letter)" -Token $token -Body $body
        if ($r.Status -ne 204) { return }
    } catch { return }
    $lastScans[$s.Letter] = ConvertTo-KcIso $now
    Write-KcJsonFile -Path $lastScansFile -Object $lastScans
    $mins = [Math]::Max(1, [int]($now - $s.StartedAt).TotalMinutes)
    Add-KcEvent -Type 'files.scanned' -Message "Drive $($s.Letter): scanned in $mins min"
    if ($s.CommandId) { Complete-Command -Id $s.CommandId -Status 'done' -Result @{ minutes = $mins } }
    foreach ($f in $s.Out, $s.Progress) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
    $files.Scan = $null
}

function Get-ScanStatus {
    $s = $files.Scan
    if ($null -eq $s) { return $null }
    $count = 0
    try { $p = Read-KcJsonFile -Path $s.Progress; if ($p) { $count = [long]$p.files } } catch { }
    @{ drive = $s.Letter; files = $count; started_at = (ConvertTo-KcIso $s.StartedAt) }
}

function Invoke-ListFolder {
    param($Command)
    $path = [string](Get-KcProp (Get-KcProp $Command 'payload') 'path' '')
    if (-not (Test-KcFolderPath $path)) {
        Complete-Command -Id $Command.id -Status 'failed' -Result @{ message = 'Not a folder path on this PC.' }
        return
    }
    try {
        $listing = Get-KcFolderListing -Path $path
        Complete-Command -Id $Command.id -Status 'done' -Result $listing
    } catch {
        Complete-Command -Id $Command.id -Status 'failed' -Result @{ message = $_.Exception.Message }
    }
}

# --- Change monitoring (roadmap #17) ---------------------------------------------------------------------------------

$monitorFile = Join-Path $paths.Config 'monitor.json'
$mon = $null
try { $mon = Read-KcJsonFile -Path $monitorFile } catch { $mon = $null }
if ($null -eq $mon) { $mon = [pscustomobject]@{ started_at = (ConvertTo-KcIso (Get-KcNow)) } }
$monSeen = @{}
$seenProp = Get-KcProp $mon 'seen'
if ($seenProp) { foreach ($p in $seenProp.PSObject.Properties) { $monSeen[$p.Name] = [string]$p.Value } }
$monRun = @{ Due = @{}; KnownPids = @{}; Dirty = $false; SavedAt = [DateTimeOffset]::MinValue; ProfilePath = $null }
# The first two days everything is "new", so programs are only learned, not reported.
$LearningHours = 48

function Test-MonDue {
    param([string]$Name, [int]$Seconds)
    $now = Get-KcNow
    $due = $monRun.Due[$Name]
    if ($null -ne $due -and $now -lt $due) { return $false }
    $monRun.Due[$Name] = $now.AddSeconds($Seconds)
    $true
}

function Set-MonValue {
    param([string]$Name, $Value)
    if ($mon.PSObject.Properties[$Name]) { $mon.$Name = $Value } else { $mon | Add-Member -NotePropertyName $Name -NotePropertyValue $Value }
    $monRun.Dirty = $true
}

function Add-MonEvents {
    param([object[]]$Events)
    foreach ($e in @($Events)) { if ($e) { Add-KcEventFromResult -Event $e } }
}

function Update-ProgramList {
    <# Compare one installed-programs list with the stored one; the first time, just remember it. #>
    param([string]$Name, $Current)
    if ($null -eq $Current) { return }
    $old = Get-KcProp $mon $Name
    if ($null -ne $old) { Add-MonEvents (Get-KcProgramChangeEvents -Old @($old) -New @($Current)) }
    Set-MonValue $Name @($Current)
}

function Invoke-ChangeMonitor {
    $now = Get-KcNow
    $learning = $false
    $started = ConvertTo-KcDate (Get-KcProp $mon 'started_at')
    if ($started -and ($now - $started).TotalHours -lt $LearningHours) { $learning = $true }

    # Programs run for the first time (and archive tools opening an archive), in the child's session.
    if ($null -ne $script:childSession -and (Test-MonDue 'processes' 2)) {
        $removable = @((Get-KcRemovableDrives) | ForEach-Object { $_.name })
        $alive = @{}
        foreach ($p in (Get-KcSessionProcesses -SessionId $script:childSession)) {
            $id = "$($p.Id)|$($p.StartTime)"
            $alive[$id] = $true
            if ($monRun.KnownPids.ContainsKey($id)) { continue }
            $monRun.KnownPids[$id] = $true
            if (-not $p.Path) { continue }
            $v = Get-KcFileVersionInfo -Path $p.Path
            $ev = Get-KcFirstRunEvent -Process $p -Seen $monSeen -Now (ConvertTo-KcIso $now) -RemovableDrives $removable -InstallRoot $InstallRoot -VersionInfo $v -Learning:$learning
            if ($ev) {
                Add-KcEventFromResult -Event $ev
                if ($ev.data.ContainsKey('flagged')) {
                    Add-KcEvent -Type 'software.vpn_detected' -Severity warning -Message "VPN or remote-control app run: $($ev.data.name)" -Data @{ name = $ev.data.name; kind = $ev.data.flagged; path = $p.Path }
                }
            }
            $monRun.Dirty = $true
            if (@('7zfm', '7zg', 'winrar', 'peazip', 'bandizip') -contains ([string]$p.Name).ToLowerInvariant()) {
                try {
                    $cmd = [string](Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$p.Id)" -ErrorAction Stop).CommandLine
                    $m = [regex]::Match($cmd, '"?([^"]+\.(zip|rar|7z|tar|gz|iso))"?', 'IgnoreCase')
                    if ($m.Success) { Add-KcEvent -Type 'archive.opened' -Message "Opened $([IO.Path]::GetFileName($m.Groups[1].Value)) in $($p.Name)" -Data @{ path = $m.Groups[1].Value; tool = $p.Name } }
                } catch { }
            }
        }
        foreach ($k in @($monRun.KnownPids.Keys)) { if (-not $alive.ContainsKey($k)) { $monRun.KnownPids.Remove($k) } }
        Limit-KcSeenPrograms -Seen $monSeen
    }

    # USB drives, with a quick look at what's on them.
    if (Test-MonDue 'usb' 10) {
        $drives = @(Get-KcRemovableDrives)
        $old = Get-KcProp $mon 'usb'
        if ($null -ne $old) {
            foreach ($d in (Compare-KcKeyed -Old @($old) -New $drives -Key 'name').Added) {
                $sum = Get-KcDriveSummary -Root $d.name
                $label = $d.label; if (-not $label) { $label = 'USB drive' }
                $gb = [Math]::Round($d.size / 1GB, 1)
                Add-KcEvent -Type 'usb.connected' -Message ("{0} ({1}, {2} GB) connected: {3} programs, {4} archives" -f $label, $d.name.TrimEnd('\'), $gb, $sum.programs, $sum.archives) `
                    -Data @{ drive = $d.name; label = $d.label; size = $d.size; programs = $sum.programs; archives = $sum.archives }
            }
        }
        Set-MonValue 'usb' $drives
    }

    # Installed programs: machine-wide, the child's own (only readable while signed in), Store apps.
    if (Test-MonDue 'programs' 60) {
        Update-ProgramList 'programs_machine' (Get-KcMachinePrograms)
        Update-ProgramList 'programs_user' (Get-KcUserPrograms -Sid $childSid)
    }
    if (Test-MonDue 'store' 600) { Update-ProgramList 'programs_store' (Get-KcStoreApps -Sid $childSid) }

    # Windows accounts and administrators.
    if (Test-MonDue 'accounts' 120) {
        $acc = Get-KcAccounts
        if ($null -ne $acc) {
            $old = Get-KcProp $mon 'accounts'
            if ($null -ne $old) { Add-MonEvents (Get-KcAccountChangeEvents -Old $old -New $acc) }
            Set-MonValue 'accounts' ([pscustomobject]$acc)
        }
    }

    # Windows Defender real-time protection.
    if (Test-MonDue 'defender' 300) {
        $def = Get-KcDefenderState
        if ($null -ne $def) {
            $old = Get-KcProp $mon 'defender'
            $was = $null
            if ($null -ne $old) { $was = [bool](Get-KcProp $old 'realtime' $true) }
            if ($null -ne $was -and $was -ne $def.realtime) {
                if ($def.realtime) { Add-KcEvent -Type 'defender.enabled' -Message 'Windows Defender real-time protection is back on' }
                else { Add-KcEvent -Type 'defender.disabled' -Severity warning -Message 'Windows Defender real-time protection was switched off' }
            }
            Set-MonValue 'defender' ([pscustomobject]$def)
        }
    }

    # Archives: new files in Downloads, Desktop, Documents; archives opened in Explorer (Recent Items).
    if (Test-MonDue 'archives' 300) {
        if (-not $monRun.ProfilePath) { $monRun.ProfilePath = Get-KcProfilePath -Sid $childSid }
        if ($monRun.ProfilePath) {
            $files = @(Get-KcArchiveFiles -ProfilePath $monRun.ProfilePath)
            $old = Get-KcProp $mon 'archives'
            if ($null -ne $old) {
                foreach ($f in (Compare-KcKeyed -Old @($old) -New $files -Key 'path').Added) {
                    $mb = [Math]::Round($f.size / 1MB, 1)
                    Add-KcEvent -Type 'archive.new' -Message "New archive: $([IO.Path]::GetFileName($f.path)) ($mb MB)" -Data @{ path = $f.path; size = $f.size }
                }
            }
            Set-MonValue 'archives' $files
            $recent = @(Get-KcRecentArchives -ProfilePath $monRun.ProfilePath)
            $oldRecent = Get-KcProp $mon 'recent_archives'
            if ($null -ne $oldRecent) {
                foreach ($r in (Compare-KcKeyed -Old @($oldRecent) -New $recent -Key 'path').Added) {
                    Add-KcEvent -Type 'archive.opened' -Message "Opened $($r.path)" -Data @{ path = $r.path; tool = 'explorer' }
                }
            }
            Set-MonValue 'recent_archives' $recent
        }
    }

    # Save at most once a minute.
    if ($monRun.Dirty -and ($now - $monRun.SavedAt).TotalSeconds -ge 60) {
        Set-MonValue 'seen' $monSeen   # a hashtable serialises straight to a JSON object
        Write-KcJsonFile -Path $monitorFile -Object $mon
        $monRun.Dirty = $false
        $monRun.SavedAt = $now
    }
}

# --- Auto-update --------------------------------------------------------------------------------------------------

function Invoke-AgentUpdate {
    <#
        The server ships a different agent bundle: download it, verify every file, swap it in, record the
        new tamper manifest, and restart. At most one attempt an hour; a failed attempt changes nothing.
    #>
    $want = Get-KcProp $state 'agent_update'
    $bundle = [string](Get-KcProp $want 'bundle' '')
    if (-not $bundle -or -not $update.Bundle -or $bundle -eq $update.Bundle) { return }
    $now = Get-KcNow
    if (($now - $update.LastAttempt).TotalHours -lt 1) { return }
    $update.LastAttempt = $now
    $from = Get-KcAgentVersion
    $to = [string](Get-KcProp $want 'version' '?')
    $stagingRoot = Join-Path $paths.Config 'update'
    try {
        Write-KcLog -Message "Updating agent $from -> $to ($bundle)"
        $staged = Receive-KcAgentUpdate -BaseUrl $apiUrl -Token $token -Expected $bundle -StagingRoot $stagingRoot
        Install-KcAgentUpdate -StagingDir $staged -InstallRoot $InstallRoot -BackupDir (Join-Path $stagingRoot 'previous')
        Write-KcJsonFile -Path $paths.Manifest -Object ([ordered]@{ created_at = (ConvertTo-KcIso (Get-KcNow)); files = (Get-KcFileHashes -InstallRoot $InstallRoot) })
        Remove-Item -LiteralPath $staged -Recurse -Force -ErrorAction SilentlyContinue
    } catch {
        Add-KcEvent -Type 'agent.update_failed' -Severity warning -Message "Update to $to failed: $($_.Exception.Message)" -Data @{ version = $to }
        return
    }
    Add-KcEvent -Type 'agent.updated' -Message "Kidputer Control updated from $from to $to" -Data @{ from = $from; to = $to; bundle = $bundle }
    [void](Send-KcQueuedEvents -BaseUrl $apiUrl -Token $token -MaxBatches 1)
    # Start again on the new files: the scheduled task's every-minute trigger would too; this is faster.
    try {
        Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\cmd.exe') -WindowStyle Hidden `
            -ArgumentList '/c timeout /t 3 /nobreak >nul & schtasks /run /tn "\KidputerControl\Watchdog"'
    } catch { }
    Stop-StrayUiAgents
    exit 0
}

# --- Tamper -------------------------------------------------------------------------------------------------------

function Invoke-TamperCheck {
    $now = Get-KcNow
    if ($now -lt $tamper.NextAt) { return }
    $tamper.NextAt = $now.AddSeconds(60)
    $problems = @(Test-KcTamper -InstallRoot $InstallRoot)
    if (($problems -join '|') -ne ($tamper.Problems -join '|')) {
        if ($problems.Count) {
            Add-KcEvent -Type 'tamper.detected' -Severity critical -Message ('Installed files changed: ' + ($problems -join '; ')) -Data @{ problems = $problems }
        } else {
            Add-KcEvent -Type 'tamper.resolved' -Message 'Installed files match the manifest again'
        }
        $tamper.Problems = $problems
    }
}

# --- Enforcement & UI state -------------------------------------------------------------------------------------------

function Update-Enforcement {
    $now = Get-KcNow
    # Drop expired local overrides so local.json doesn't grow.
    $live = @($local.local_overrides | Where-Object { $e = ConvertTo-KcDate $_.ends_at; $e -and $e -gt $now })
    if ($live.Count -ne @($local.local_overrides).Count) { $local.local_overrides = $live; $script:localDirty = $true }

    $decision = Get-KcEnforcementDecision -Now $now -State $state -Local $local
    $beforeEpisode = ConvertTo-Json -InputObject $local.lock_episode -Compress
    $mode = Update-KcLockEpisode -Decision $decision -Local $local -Now $now -CountdownSeconds ([int](Get-KcProp $state 'countdown_seconds' 300))
    if ((ConvertTo-Json -InputObject $local.lock_episode -Compress) -ne $beforeEpisode) { $script:localDirty = $true }
    $script:currentDecision = $decision
    $script:currentMode = $mode

    if ($mode.Mode -ne $script:lastMode) {
        switch ($mode.Mode) {
            'countdown' { Add-KcEvent -Type 'session.countdown_started' -Message "Locking at $(ConvertTo-KcIso $mode.CountdownEndsAt) ($($decision.Reason))" }
            'locked' {
                $data = @{ reason = $decision.Reason }
                if ($decision.LockRequestId) { $data.lock_request_id = $decision.LockRequestId }
                Add-KcEvent -Type 'session.locked' -Message "PC locked ($($decision.Reason))" -Data $data
                if ($decision.Reason -eq 'immediate') {
                    Add-KcEvent -Type 'lock.applied' -Message 'Immediate lock applied' -Data @{ lock_request_id = $decision.LockRequestId }
                }
            }
            'none' { if ($null -ne $script:lastMode) { Add-KcEvent -Type 'session.unlocked' -Message "PC unlocked ($($decision.Reason))" } }
        }
        $script:lastMode = $mode.Mode
    }

    $pinCfg = Get-KcProp $state 'pin'
    $lockReasonText = $null
    if ($decision.Reason -eq 'immediate') { $lockReasonText = Get-KcProp (Get-KcProp $state 'immediate_lock') 'reason' }
    $msg = Get-ActiveMessage
    $uiState = [ordered]@{
        mode              = $mode.Mode
        reason            = $decision.Reason
        countdown_ends_at = $(if ($mode.CountdownEndsAt) { ConvertTo-KcIso $mode.CountdownEndsAt } else { $null })
        unlock_at         = $(if ($decision.UnlockAt) { ConvertTo-KcIso $decision.UnlockAt } else { $null })
        lock_reason_text  = $lockReasonText
        pin_enabled       = [bool](Get-KcProp $pinCfg 'hash')
        pin_locked_until  = $local.pin_locked_until
        pin_result        = $script:pinResult
        message           = $(if ($msg) { [ordered]@{ id = [string]$msg.id; body = [string]$msg.body; mode = [string]$msg.mode } } else { $null })
        device_name       = Get-KcProp $state 'name'
        app_warnings      = @($appStops | Where-Object { $null -eq $_.ForceAt } | ForEach-Object { [ordered]@{ name = $_.Name; ends_at = (ConvertTo-KcIso $_.EndsAt) } })
        app_closing       = @($appStops | Where-Object { $null -ne $_.ForceAt } | ForEach-Object { $_.Path })
        notices           = @($appRun.Notices | ForEach-Object { [ordered]@{ text = $_.text; until = (ConvertTo-KcIso $_.until) } })
    }
    $json = ConvertTo-Json -InputObject $uiState -Depth 5 -Compress
    if ($json -ne $script:lastUiJson) {
        Write-KcJsonFile -Path $paths.UiState -Object $uiState
        $script:lastUiJson = $json
    }
}

# --- Power: shutdown and sleep -------------------------------------------------------------------------------------

function Update-PowerEvents {
    <#
        Tell the server why the PC is about to go quiet, so parents get "turned off" rather than a
        "PC went silent" alarm. Shutdown: flush the event right away (Windows gives us a few seconds).
        Sleep: the loop simply stops; a long gap between ticks means the PC was asleep.
    #>
    $now = Get-KcNow
    if ($power.LastTick -and ($now - $power.LastTick).TotalSeconds -gt 60) {
        Add-KcEvent -Type 'pc.resumed' -Message "PC woke up (asleep since $(ConvertTo-KcIso $power.LastTick))" -Data @{ asleep_from = (ConvertTo-KcIso $power.LastTick) }
        $sync.Phase = 'events'
        $sync.NextAt = $now
    }
    $power.LastTick = $now
    $ending = $false
    try { $ending = [KcShutdownWatcher]::Ending } catch { }
    if ($ending -and -not $power.ShutdownReported) {
        $power.ShutdownReported = $true
        Add-KcEvent -Type 'pc.shutting_down' -Message 'Windows is shutting down or restarting'
        if ($token -and $apiUrl) { [void](Send-KcQueuedEvents -BaseUrl $apiUrl -Token $token -MaxBatches 1) }
    }
}

function Get-KcBootTime {
    try { return ConvertTo-KcIso ([DateTimeOffset](Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime) } catch { return $null }
}

# --- Main loop -------------------------------------------------------------------------------------------------------

# boot_time lets the server tell "the PC was off" from "Kidputer Control was stopped while Windows ran".
Add-KcEvent -Type 'agent.started' -Message "Watchdog $(Get-KcAgentVersion) started on $([Environment]::MachineName)" -Data @{ boot_time = (Get-KcBootTime) }
if ($null -eq $token) { Add-KcEvent -Type 'agent.unauthorized' -Severity critical -Message 'No device token; enforcing from cache only' }
Update-ChildAdminStatus
# In Trusted mode an administrator child is expected; in Managed mode it's a hole in enforcement.
if ($adminCheck.IsAdmin -and (Get-KcProp $state 'mode' 'managed') -ne 'trusted') {
    Add-KcEvent -Type 'agent.child_is_admin' -Severity critical -Message 'The monitored account is a local administrator and could disable enforcement'
}

while ($true) {
    try {
        Update-PowerEvents
        Invoke-Inbox -Decision $script:currentDecision
        Update-Enforcement
        if ($script:localDirty) { Save-Local; $script:localDirty = $false }
        Update-UiAgent
        Invoke-Commands
        Invoke-AppRules
        Update-AppStops
        Update-DriveScan
        Invoke-SyncStep
        Invoke-TamperCheck
        Update-ChildAdminStatus
        try { Invoke-ChangeMonitor } catch { Write-KcLog -Level warning -Message "Change monitor: $($_.Exception.Message)" }
    } catch {
        Write-KcLog -Level error -Message ("Watchdog loop error: {0} {1}" -f $_.Exception.Message, $_.InvocationInfo.PositionMessage)
        Start-Sleep -Seconds 2
    }
    Start-Sleep -Milliseconds 1000
}
