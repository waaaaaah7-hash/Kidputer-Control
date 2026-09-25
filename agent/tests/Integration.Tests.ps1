<#
    Agent <-> API protocol test against a live server. Driven by server/tests/test_agent_integration.py,
    which starts the API, creates an admin/device and passes:
      SM_IT_API_URL, SM_IT_ENROLL_CODE, SM_IT_PIN
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TestHarness.ps1')
Import-Module (Join-Path $PSScriptRoot '..\SessionMonitor\Modules\SessionMonitor.Common.psm1') -Force

$api = $env:SM_IT_API_URL
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("sm-it-" + [guid]::NewGuid().ToString('N'))
$paths = Initialize-SmPaths -DataRoot $tmp
foreach ($d in $paths.Config, $paths.Logs, $paths.Ipc, $paths.Inbox) { [void][IO.Directory]::CreateDirectory($d) }

$script:token = $null
$script:state = $null

Test 'enroll with the one-time code' {
    $r = Invoke-SmEnroll -BaseUrl $api -EnrollmentCode $env:SM_IT_ENROLL_CODE
    Assert-True ([string]$r.device_token).Length 'token'
    Save-SmDeviceToken -Token $r.device_token
    $script:token = Get-SmDeviceToken
}

Test 'sync pulls and caches state, then gets 304' {
    $r = Sync-SmState -BaseUrl $api -Token $script:token -CachedState $null
    Assert-Equal $true $r.Changed
    Assert-True ($null -ne $r.ServerTime) 'server time header'
    $script:state = Read-SmJsonFile -Path $paths.StateFile
    Assert-Equal 'Integration PC' $script:state.name
    Assert-True (@($script:state.lock_windows).Count -gt 0) 'lock windows'
    $again = Sync-SmState -BaseUrl $api -Token $script:token -CachedState $script:state
    Assert-Equal $false $again.Changed; Assert-Equal $false $again.Offline
}

Test 'PIN set in the dashboard verifies offline from the cache' {
    Assert-Equal $true (Test-SmPin -Pin $env:SM_IT_PIN -PinConfig $script:state.pin)
    Assert-Equal $false (Test-SmPin -Pin '99999999' -PinConfig $script:state.pin)
}

Test 'a PIN unlock is queued and uploaded; retrying the upload is idempotent' {
    $local = New-SmLocalState
    $now = Get-SmNow
    $decision = Get-SmEnforcementDecision -Now $now -State $script:state -Local $local
    Assert-Equal 'immediate' $decision.Reason
    $r = Invoke-SmPinAttempt -Pin $env:SM_IT_PIN -State $script:state -Local $local -Now $now -Decision $decision
    Assert-Equal $true $r.Ok
    foreach ($e in $r.Events) { Add-SmEventFromResult -Event $e }
    Add-SmEvent -Type 'tamper.detected' -Severity critical -Message 'integration test' -Data @{ problems = @('modified: Watchdog.ps1') }
    $lines = Get-SmQueuedEvents
    Assert-Equal 3 $lines.Count
    Assert-Equal 3 (Send-SmQueuedEvents -BaseUrl $api -Token $script:token)
    Assert-Equal 0 (Get-SmQueuedEvents).Count
    Set-SmQueuedEvents -Lines $lines   # simulate a lost response: the same events get sent again
    Assert-Equal 3 (Send-SmQueuedEvents -BaseUrl $api -Token $script:token)
}

Test 'server state reflects the PIN unlock' {
    $r = Sync-SmState -BaseUrl $api -Token $script:token -CachedState $script:state
    Assert-Equal $true $r.Changed
    Assert-Equal $null $r.State.immediate_lock
    Assert-Equal 1 @($r.State.overrides).Count
}

Test 'heartbeat' {
    Assert-Equal $true (Send-SmHeartbeat -BaseUrl $api -Token $script:token -Status @{ mode = 'none'; ui_agent_alive = $true })
}

Test 'offline API is reported, not thrown' {
    $r = Sync-SmState -BaseUrl 'http://127.0.0.1:9' -Token $script:token -CachedState $script:state
    Assert-Equal $true $r.Offline
    Add-SmEvent -Type 'agent.started' -Message 'queued while offline'
    Assert-Equal 0 (Send-SmQueuedEvents -BaseUrl 'http://127.0.0.1:9' -Token $script:token)
    Assert-Equal 1 (Get-SmQueuedEvents).Count
}

Test 'revoked token is reported as unauthorized' {
    $r = Sync-SmState -BaseUrl $api -Token 'revoked' -CachedState $script:state
    Assert-Equal $true $r.Unauthorized
}

Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
Complete-Tests
