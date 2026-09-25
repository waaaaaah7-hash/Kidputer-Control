#Requires -Version 5.1
<#
.SYNOPSIS
    Session Monitor SYSTEM watchdog. Owns enforcement, tamper detection and cloud sync.

.DESCRIPTION
    Runs as SYSTEM from the "\SessionMonitor\Watchdog" scheduled task (at startup, re-triggered every
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
    [string]$DataRoot = (Join-Path $env:ProgramData 'SessionMonitor')
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $InstallRoot 'Modules\SessionMonitor.Common.psm1') -Force
$paths = Initialize-SmPaths -DataRoot $DataRoot

# --- Single instance ------------------------------------------------------------------------------------
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, 'Global\SessionMonitorWatchdog', [ref]$createdNew)
if (-not $createdNew) {
    try {
        # A previous instance that died without releasing leaves the mutex abandoned: take it over.
        if (-not $mutex.WaitOne(0)) { exit 0 }
    } catch [System.Threading.AbandonedMutexException] { }
}

Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $InstallRoot 'Modules\SessionMonitor.Native.cs')))

$agentCfg = Read-SmJsonFile -Path $paths.AgentFile
if ($null -eq $agentCfg) { Write-SmLog -Level error -Message 'agent.json missing; reinstall required'; exit 1 }
$apiUrl = [string]$agentCfg.api_url
$childSid = [string]$agentCfg.child_user_sid
$token = $null
try { $token = Get-SmDeviceToken } catch { Write-SmLog -Level error -Message "Cannot read device token: $($_.Exception.Message)" }

$state = $null
try { $state = Read-SmJsonFile -Path $paths.StateFile } catch { Write-SmLog -Level warning -Message 'Cached state unreadable; waiting for sync' }
$local = Read-SmLocalState

$uiScript = Join-Path $InstallRoot 'UiAgent.ps1'
$powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$uiCmdLine = '"{0}" -NoProfile -NonInteractive -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "{1}" -DataRoot "{2}"' -f $powershellExe, $uiScript, $DataRoot

# --- Runtime state ---------------------------------------------------------------------------------------
$ui = @{ Pid = 0; StartTime = $null; SessionId = [SmNative]::NoSession; Launches = New-Object System.Collections.Queue; EverStarted = $false }
$sync = @{ Phase = 'state'; NextAt = [DateTimeOffset]::MinValue; Failures = 0; LastOkAt = $null; Offline = $true; Unauthorized = $false; SkewReportedAt = [DateTimeOffset]::MinValue; SkewSeconds = $null }
$tamper = @{ NextAt = [DateTimeOffset]::MinValue; Problems = @() }
$lastMode = $null
$lastUiJson = $null
$pinResult = $null
$consoleIsChild = $false
$localDirty = $false

function Save-Local {
    Write-SmJsonFile -Path $paths.LocalFile -Object $local
}

function Get-PollSeconds {
    $s = [int](Get-SmProp $state 'poll_interval_seconds' 30)
    [Math]::Min([Math]::Max($s, 5), 3600)
}

function Test-ChildIsAdmin {
    try {
        $admins = Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop
        return [bool]($admins | Where-Object { $_.SID -and $_.SID.Value -eq $childSid })
    } catch { return $false }
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
    $session = [SmNative]::ActiveConsoleSessionId()
    $sid = $null
    if ($session -ne [SmNative]::NoSession) { $sid = [SmNative]::GetSessionUserSid($session) }
    $script:consoleIsChild = ($null -ne $sid) -and ($sid -eq $childSid)
    if (-not $script:consoleIsChild) { return }   # enforcement applies to the child's account only

    if ((Test-UiAlive) -and $ui.SessionId -eq $session) { return }

    $now = Get-SmNow
    while ($ui.Launches.Count -and ($now - $ui.Launches.Peek()).TotalSeconds -gt 60) { [void]$ui.Launches.Dequeue() }
    if ($ui.Launches.Count -ge 20) { return }   # crash loop: back off, keep retrying next minute

    if ($ui.EverStarted -and $ui.Pid -gt 0) {
        $why = 'exited'
        if ($ui.SessionId -ne $session) { $why = 'session changed' }
        Add-SmEvent -Type 'ui_agent.restarted' -Severity warning -Message "UI agent $why; relaunching" -Data @{ previous_pid = $ui.Pid }
    }
    Stop-StrayUiAgents
    try {
        $newPid = [SmNative]::LaunchInSession($session, $powershellExe, $uiCmdLine, $InstallRoot)
        $proc = Get-Process -Id $newPid -ErrorAction SilentlyContinue
        $ui.Pid = $newPid
        $ui.StartTime = $null
        if ($proc) { $ui.StartTime = $proc.StartTime }
        $ui.SessionId = $session
        $ui.EverStarted = $true
        $ui.Launches.Enqueue($now)
        Write-SmLog -Message "UI agent started (pid $newPid, session $session)"
    } catch {
        $ui.Launches.Enqueue($now)
        Write-SmLog -Level error -Message "UI agent launch failed: $($_.Exception.Message)"
    }
}

# --- Inbox (requests from the UI agent; untrusted: they come from the child's account) ------------------------------

function Get-ActiveMessage {
    $m = Get-SmProp $state 'message'
    if ($null -eq $m) { return $null }
    if (@($local.dismissed_messages) -contains [string]$m.id) { return $null }
    $m
}

function Invoke-Inbox {
    param([hashtable]$Decision)
    $handled = $false
    foreach ($req in (Get-SmInboxRequests)) {
        $type = [string](Get-SmProp $req 'type')
        $requestId = [string](Get-SmProp $req 'request_id' '')
        if ($requestId.Length -gt 64) { continue }
        switch ($type) {
            'pin_attempt' {
                $pin = [string](Get-SmProp $req 'pin' '')
                if ($pin.Length -gt 32) { $pin = '' }
                $r = Invoke-SmPinAttempt -Pin $pin -State $state -Local $local -Now (Get-SmNow) -Decision $Decision
                foreach ($e in $r.Events) { Add-SmEventFromResult -Event $e }
                $script:pinResult = @{ request_id = $requestId; ok = $r.Ok; message = $r.Message }
                $script:localDirty = $true
                $handled = $true
            }
            'message_displayed' {
                $current = Get-ActiveMessage
                $id = [string](Get-SmProp $req 'message_id' '')
                if ($current -and $id -eq [string]$current.id -and @($local.displayed_messages) -notcontains $id) {
                    $local.displayed_messages = Add-SmBoundedItem -List $local.displayed_messages -Item $id
                    Add-SmEvent -Type 'message.displayed' -Message 'Message shown on screen' -Data @{ message_id = $id }
                    $script:localDirty = $true
                    $handled = $true
                }
            }
            'message_closed' {
                $id = [string](Get-SmProp $req 'message_id' '')
                $reason = [string](Get-SmProp $req 'reason' '')
                $current = Get-ActiveMessage
                if ($reason -eq 'user' -and $current -and $id -eq [string]$current.id) {
                    if ($current.mode -eq 'closable') {
                        $local.dismissed_messages = Add-SmBoundedItem -List $local.dismissed_messages -Item $id
                        Add-SmEvent -Type 'message.dismissed' -Message 'Message closed by the child' -Data @{ message_id = $id }
                        $script:localDirty = $true
                    } else {
                        Add-SmEvent -Type 'message.close_blocked' -Severity warning -Message 'Attempt to close a persistent message' -Data @{ message_id = $id }
                    }
                    $handled = $true
                }
            }
            'ui_error' {
                $msg = [string](Get-SmProp $req 'message' '')
                if ($msg.Length -gt 500) { $msg = $msg.Substring(0, 500) }
                Add-SmEvent -Type 'ui_agent.error' -Severity warning -Message $msg
                $handled = $true
            }
        }
    }
    if ($handled) {
        # Get acknowledgements (and PIN overrides) to the dashboard promptly.
        $sync.Phase = 'events'
        $sync.NextAt = (Get-SmNow).AddSeconds(2)
    }
}

# --- Cloud sync: one HTTP call per tick ------------------------------------------------------------------------------

function Invoke-SyncStep {
    $now = Get-SmNow
    if ($now -lt $sync.NextAt -or -not $token -or -not $apiUrl) { return }
    switch ($sync.Phase) {
        'state' {
            $r = Sync-SmState -BaseUrl $apiUrl -Token $token -CachedState $state
            if ($r.Unauthorized) {
                if (-not $sync.Unauthorized) { Add-SmEvent -Type 'agent.unauthorized' -Severity critical -Message 'Device token rejected; enforcing from cache. Re-enroll this PC.' }
                $sync.Unauthorized = $true
            } else { $sync.Unauthorized = $false }
            if ($r.Offline) {
                if (-not $sync.Offline) { Write-SmLog -Level warning -Message "Cloud unreachable: $($r.Error)" }
                $sync.Offline = $true
                $sync.Failures++
                $backoff = [Math]::Min((Get-PollSeconds) * [Math]::Pow(2, [Math]::Min($sync.Failures, 6)), 300)
                $sync.NextAt = $now.AddSeconds($backoff)
                return
            }
            if ($sync.Offline -and $sync.LastOkAt) { Add-SmEvent -Type 'agent.reconnected' -Message 'Cloud connection restored' }
            $sync.Offline = $false
            $sync.Failures = 0
            $sync.LastOkAt = $now
            if ($r.Changed) { $script:state = $r.State; Write-SmLog -Message 'New configuration received' }
            if ($r.ServerTime) {
                $sync.SkewSeconds = [int]($now - $r.ServerTime).TotalSeconds
                if ([Math]::Abs($sync.SkewSeconds) -gt 300 -and ($now - $sync.SkewReportedAt).TotalHours -ge 6) {
                    Add-SmEvent -Type 'clock.skew' -Severity warning -Message "PC clock differs from server by $($sync.SkewSeconds)s" -Data @{ skew_seconds = $sync.SkewSeconds }
                    $sync.SkewReportedAt = $now
                }
            }
            $sync.Phase = 'events'
        }
        'events' {
            [void](Send-SmQueuedEvents -BaseUrl $apiUrl -Token $token -MaxBatches 1)
            if ((Get-SmQueuedEvents).Count -eq 0) { $sync.Phase = 'heartbeat' }
        }
        'heartbeat' {
            [void](Send-SmHeartbeat -BaseUrl $apiUrl -Token $token -Status (Get-Status))
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
        queued_events         = (Get-SmQueuedEvents).Count
        clock_skew_seconds    = $sync.SkewSeconds
        pin_locked_until      = $local.pin_locked_until
    }
    if ($d -and $d.UnlockAt) { $s.unlock_at = ConvertTo-SmIso $d.UnlockAt }
    if ($d -and $d.OverrideEndsAt) { $s.override_ends_at = ConvertTo-SmIso $d.OverrideEndsAt }
    $s
}

# --- Tamper -------------------------------------------------------------------------------------------------------

function Invoke-TamperCheck {
    $now = Get-SmNow
    if ($now -lt $tamper.NextAt) { return }
    $tamper.NextAt = $now.AddSeconds(60)
    $problems = @(Test-SmTamper -InstallRoot $InstallRoot)
    if (($problems -join '|') -ne ($tamper.Problems -join '|')) {
        if ($problems.Count) {
            Add-SmEvent -Type 'tamper.detected' -Severity critical -Message ('Installed files changed: ' + ($problems -join '; ')) -Data @{ problems = $problems }
        } else {
            Add-SmEvent -Type 'tamper.resolved' -Message 'Installed files match the manifest again'
        }
        $tamper.Problems = $problems
    }
}

# --- Enforcement & UI state -------------------------------------------------------------------------------------------

function Update-Enforcement {
    $now = Get-SmNow
    # Drop expired local overrides so local.json doesn't grow.
    $live = @($local.local_overrides | Where-Object { $e = ConvertTo-SmDate $_.ends_at; $e -and $e -gt $now })
    if ($live.Count -ne @($local.local_overrides).Count) { $local.local_overrides = $live; $script:localDirty = $true }

    $decision = Get-SmEnforcementDecision -Now $now -State $state -Local $local
    $beforeEpisode = ConvertTo-Json -InputObject $local.lock_episode -Compress
    $mode = Update-SmLockEpisode -Decision $decision -Local $local -Now $now -CountdownSeconds ([int](Get-SmProp $state 'countdown_seconds' 300))
    if ((ConvertTo-Json -InputObject $local.lock_episode -Compress) -ne $beforeEpisode) { $script:localDirty = $true }
    $script:currentDecision = $decision
    $script:currentMode = $mode

    if ($mode.Mode -ne $script:lastMode) {
        switch ($mode.Mode) {
            'countdown' { Add-SmEvent -Type 'session.countdown_started' -Message "Locking at $(ConvertTo-SmIso $mode.CountdownEndsAt) ($($decision.Reason))" }
            'locked' {
                $data = @{ reason = $decision.Reason }
                if ($decision.LockRequestId) { $data.lock_request_id = $decision.LockRequestId }
                Add-SmEvent -Type 'session.locked' -Message "PC locked ($($decision.Reason))" -Data $data
                if ($decision.Reason -eq 'immediate') {
                    Add-SmEvent -Type 'lock.applied' -Message 'Immediate lock applied' -Data @{ lock_request_id = $decision.LockRequestId }
                }
            }
            'none' { if ($null -ne $script:lastMode) { Add-SmEvent -Type 'session.unlocked' -Message "PC unlocked ($($decision.Reason))" } }
        }
        $script:lastMode = $mode.Mode
    }

    $pinCfg = Get-SmProp $state 'pin'
    $lockReasonText = $null
    if ($decision.Reason -eq 'immediate') { $lockReasonText = Get-SmProp (Get-SmProp $state 'immediate_lock') 'reason' }
    $msg = Get-ActiveMessage
    $uiState = [ordered]@{
        mode              = $mode.Mode
        reason            = $decision.Reason
        countdown_ends_at = $(if ($mode.CountdownEndsAt) { ConvertTo-SmIso $mode.CountdownEndsAt } else { $null })
        unlock_at         = $(if ($decision.UnlockAt) { ConvertTo-SmIso $decision.UnlockAt } else { $null })
        lock_reason_text  = $lockReasonText
        pin_enabled       = [bool](Get-SmProp $pinCfg 'hash')
        pin_locked_until  = $local.pin_locked_until
        pin_result        = $script:pinResult
        message           = $(if ($msg) { [ordered]@{ id = [string]$msg.id; body = [string]$msg.body; mode = [string]$msg.mode } } else { $null })
        device_name       = Get-SmProp $state 'name'
    }
    $json = ConvertTo-Json -InputObject $uiState -Depth 5 -Compress
    if ($json -ne $script:lastUiJson) {
        Write-SmJsonFile -Path $paths.UiState -Object $uiState
        $script:lastUiJson = $json
    }
}

# --- Main loop -------------------------------------------------------------------------------------------------------

Add-SmEvent -Type 'agent.started' -Message "Watchdog $(Get-SmAgentVersion) started on $([Environment]::MachineName)"
if ($null -eq $token) { Add-SmEvent -Type 'agent.unauthorized' -Severity critical -Message 'No device token; enforcing from cache only' }
if (Test-ChildIsAdmin) {
    Add-SmEvent -Type 'agent.child_is_admin' -Severity critical -Message 'The monitored account is a local administrator and could disable enforcement'
}

while ($true) {
    try {
        Invoke-Inbox -Decision $script:currentDecision
        Update-Enforcement
        if ($script:localDirty) { Save-Local; $script:localDirty = $false }
        Update-UiAgent
        Invoke-SyncStep
        Invoke-TamperCheck
    } catch {
        Write-SmLog -Level error -Message ("Watchdog loop error: {0} {1}" -f $_.Exception.Message, $_.InvocationInfo.PositionMessage)
        Start-Sleep -Seconds 2
    }
    Start-Sleep -Milliseconds 1000
}
