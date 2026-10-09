<#
    Agent <-> API protocol test against a live server. Driven by server/tests/test_agent_integration.py,
    which starts the API, creates an admin/device and passes:
      KC_IT_API_URL, KC_IT_ENROLL_CODE, KC_IT_PIN
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TestHarness.ps1')
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Common.psm1') -Force
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Apps.psm1') -Force

$api = $env:KC_IT_API_URL
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("kc-it-" + [guid]::NewGuid().ToString('N'))
$paths = Initialize-KcPaths -DataRoot $tmp
foreach ($d in $paths.Config, $paths.Logs, $paths.Ipc, $paths.Inbox) { [void][IO.Directory]::CreateDirectory($d) }

$script:token = $null
$script:state = $null

Test 'enroll with the one-time code' {
    $r = Invoke-KcEnroll -BaseUrl $api -EnrollmentCode $env:KC_IT_ENROLL_CODE
    Assert-True ([string]$r.device_token).Length 'token'
    Save-KcDeviceToken -Token $r.device_token
    $script:token = Get-KcDeviceToken
}

Test 'sync pulls and caches state, then gets 304' {
    $r = Sync-KcState -BaseUrl $api -Token $script:token -CachedState $null
    Assert-Equal $true $r.Changed
    Assert-True ($null -ne $r.ServerTime) 'server time header'
    $script:state = Read-KcJsonFile -Path $paths.StateFile
    Assert-Equal 'Integration PC' $script:state.name
    Assert-True (@($script:state.lock_windows).Count -gt 0) 'lock windows'
    $again = Sync-KcState -BaseUrl $api -Token $script:token -CachedState $script:state
    Assert-Equal $false $again.Changed; Assert-Equal $false $again.Offline
}

Test 'PIN set in the dashboard verifies offline from the cache' {
    Assert-Equal $true (Test-KcPin -Pin $env:KC_IT_PIN -PinConfig $script:state.pin)
    Assert-Equal $false (Test-KcPin -Pin '99999999' -PinConfig $script:state.pin)
}

Test 'a PIN unlock is queued and uploaded; retrying the upload is idempotent' {
    $local = New-KcLocalState
    $now = Get-KcNow
    $decision = Get-KcEnforcementDecision -Now $now -State $script:state -Local $local
    Assert-Equal 'immediate' $decision.Reason
    $r = Invoke-KcPinAttempt -Pin $env:KC_IT_PIN -State $script:state -Local $local -Now $now -Decision $decision
    Assert-Equal $true $r.Ok
    foreach ($e in $r.Events) { Add-KcEventFromResult -Event $e }
    Add-KcEvent -Type 'tamper.detected' -Severity critical -Message 'integration test' -Data @{ problems = @('modified: Watchdog.ps1') }
    $lines = Get-KcQueuedEvents
    Assert-Equal 3 $lines.Count
    Assert-Equal 3 (Send-KcQueuedEvents -BaseUrl $api -Token $script:token)
    Assert-Equal 0 (Get-KcQueuedEvents).Count
    Set-KcQueuedEvents -Lines $lines   # simulate a lost response: the same events get sent again
    Assert-Equal 3 (Send-KcQueuedEvents -BaseUrl $api -Token $script:token)
}

Test 'server state reflects the PIN unlock' {
    $r = Sync-KcState -BaseUrl $api -Token $script:token -CachedState $script:state
    Assert-Equal $true $r.Changed
    Assert-Equal $null $r.State.immediate_lock
    Assert-Equal 1 @($r.State.overrides).Count
}

Test 'heartbeat' {
    Assert-Equal $true (Send-KcHeartbeat -BaseUrl $api -Token $script:token -Status @{ mode = 'none'; ui_agent_alive = $true })
}

Test 'a dashboard command arrives, and its result and a process report go back' {
    $cmds = @($script:state.commands)
    Assert-Equal 1 $cmds.Count
    Assert-Equal 'stop_app' $cmds[0].kind
    Assert-Equal 'Minecraft' $cmds[0].payload.name
    $procs = @([pscustomobject]@{ Id = 4242; Name = 'javaw'; Path = 'C:\Games\javaw.exe'; StartTime = $null; MemoryMB = 900; HasWindow = $true; Title = $null })
    $report = @{ child_signed_in = $true; processes = (ConvertTo-KcProcessReport -Processes $procs -VersionLookup { param($p) @{ product = 'Minecraft'; description = 'Minecraft'; company = 'Mojang' } }) }
    Assert-Equal $true (Send-KcReport -BaseUrl $api -Token $script:token -Kind 'processes' -Data $report)
    $result = [pscustomobject]@{ id = $cmds[0].id; status = 'done'; result = @{ closed = 1; forced = 0 } }
    Assert-Equal $true (Send-KcCommandResult -BaseUrl $api -Token $script:token -Result $result)
    # A result for a command the server no longer knows counts as delivered, so it isn't retried forever.
    $gone = [pscustomobject]@{ id = [guid]::NewGuid().ToString(); status = 'done'; result = $null }
    Assert-Equal $true (Send-KcCommandResult -BaseUrl $api -Token $script:token -Result $gone)
}

Test 'offline API is reported, not thrown' {
    $r = Sync-KcState -BaseUrl 'http://127.0.0.1:9' -Token $script:token -CachedState $script:state
    Assert-Equal $true $r.Offline
    Add-KcEvent -Type 'agent.started' -Message 'queued while offline'
    Assert-Equal 0 (Send-KcQueuedEvents -BaseUrl 'http://127.0.0.1:9' -Token $script:token)
    Assert-Equal 1 (Get-KcQueuedEvents).Count
}

Test 'revoked token is reported as unauthorized' {
    $r = Sync-KcState -BaseUrl $api -Token 'revoked' -CachedState $script:state
    Assert-Equal $true $r.Unauthorized
}

Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
Complete-Tests
