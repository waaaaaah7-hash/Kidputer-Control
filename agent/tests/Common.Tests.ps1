<#
    Unit tests for KidputerControl.Common.psm1. Self-contained (no Pester needed) so they run the same on
    Windows PowerShell 5.1 and PowerShell 7:  powershell -File agent\tests\Common.Tests.ps1
    Exits non-zero on failure.
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TestHarness.ps1')
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Common.psm1') -Force

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("kc-test-" + [guid]::NewGuid().ToString('N'))
$paths = Initialize-KcPaths -DataRoot $tmp
foreach ($d in $paths.Config, $paths.Logs, $paths.Ipc, $paths.Inbox) { [void][IO.Directory]::CreateDirectory($d) }

function D([string]$s) { [DateTimeOffset]::Parse($s, [Globalization.CultureInfo]::InvariantCulture) }
$utc = [TimeZoneInfo]::Utc

function New-State {
    param([hashtable]$Extra = @{})
    $s = [ordered]@{
        enforcement_enabled = $true
        countdown_seconds   = 300
        schedule            = @(@{ day_of_week = 0; lock_at = '21:00'; unlock_at = '07:00' })
        lock_windows        = @(@{ start = '2026-01-12T21:00:00+00:00'; end = '2026-01-13T07:00:00+00:00' })
        lock_windows_until  = '2026-01-26T00:00:00+00:00'
        overrides           = @()
        immediate_lock      = $null
        message             = $null
        pin                 = @{ algorithm = 'pbkdf2-sha256'; hash = '6I8CYMVtq/C0ZAINSuwXruaP3CqSpH6li0CV0jE4f2o='; salt = 'AAECAwQFBgcICQoLDA0ODw=='; iterations = 1000; max_attempts = 3; lockout_minutes = 15; override_minutes = 60 }
    }
    foreach ($k in $Extra.Keys) { $s[$k] = $Extra[$k] }
    # Round-trip through JSON so tests see exactly what the agent sees from the cache.
    (ConvertTo-Json -InputObject $s -Depth 10) | ConvertFrom-Json
}

# --- Decision ----------------------------------------------------------------------------------------

Test 'no cached config means nothing to enforce' {
    $d = Get-KcEnforcementDecision -Now (D '2026-01-12T22:00:00Z') -State $null
    Assert-Equal $false $d.Locked; Assert-Equal 'no_config' $d.Reason
}

Test 'locked inside a precomputed window, with the window end as unlock time' {
    $d = Get-KcEnforcementDecision -Now (D '2026-01-12T22:00:00Z') -State (New-State) -TimeZone $utc
    Assert-Equal $true $d.Locked; Assert-Equal 'schedule' $d.Reason
    Assert-Equal (D '2026-01-13T07:00:00Z') $d.UnlockAt
    Assert-Equal 'schedule:2026-01-12T21:00:00.000Z' $d.EpisodeKey
}

Test 'unlocked outside windows' {
    $d = Get-KcEnforcementDecision -Now (D '2026-01-13T07:00:00Z') -State (New-State) -TimeZone $utc
    Assert-Equal $false $d.Locked; Assert-Equal 'none' $d.Reason
}

Test 'enforcement disabled' {
    $d = Get-KcEnforcementDecision -Now (D '2026-01-12T22:00:00Z') -State (New-State @{ enforcement_enabled = $false })
    Assert-Equal $false $d.Locked; Assert-Equal 'disabled' $d.Reason
}

Test 'server override beats the schedule' {
    $st = New-State @{ overrides = @(@{ id = 'o1'; starts_at = '2026-01-12T21:30:00Z'; ends_at = '2026-01-12T23:00:00Z' }) }
    $d = Get-KcEnforcementDecision -Now (D '2026-01-12T22:00:00Z') -State $st
    Assert-Equal $false $d.Locked; Assert-Equal 'override' $d.Reason
    Assert-Equal (D '2026-01-12T23:00:00Z') $d.OverrideEndsAt
    $after = Get-KcEnforcementDecision -Now (D '2026-01-12T23:00:00Z') -State $st
    Assert-Equal 'schedule' $after.Reason
}

Test 'local PIN override counts as an override' {
    $local = New-KcLocalState
    $local.local_overrides = @([pscustomobject]@{ starts_at = '2026-01-12T21:50:00.000Z'; ends_at = '2026-01-12T22:50:00.000Z' })
    $d = Get-KcEnforcementDecision -Now (D '2026-01-12T22:00:00Z') -State (New-State) -Local $local
    Assert-Equal 'override' $d.Reason
}

Test 'immediate lock beats an active override without cancelling it' {
    $st = New-State @{
        overrides      = @(@{ id = 'o1'; starts_at = '2026-01-12T10:00:00Z'; ends_at = '2026-01-12T23:00:00Z' })
        immediate_lock = @{ id = 'L1'; reason = 'chores'; requested_at = '2026-01-12T12:00:00Z' }
    }
    $d = Get-KcEnforcementDecision -Now (D '2026-01-12T12:00:00Z') -State $st
    Assert-Equal $true $d.Locked; Assert-Equal 'immediate' $d.Reason; Assert-Equal 'L1' $d.LockRequestId
    $local = New-KcLocalState; $local.released_locks = @('L1')
    $released = Get-KcEnforcementDecision -Now (D '2026-01-12T12:00:00Z') -State $st -Local $local
    Assert-Equal 'override' $released.Reason
}

Test 'weekly fallback once precomputed windows run out (crosses midnight)' {
    $st = New-State @{ lock_windows = @(); lock_windows_until = '2026-01-01T00:00:00Z' }
    # 2026-02-03 is a Tuesday; Monday's 21:00-07:00 window covers 02:00.
    $d = Get-KcEnforcementDecision -Now (D '2026-02-03T02:00:00Z') -State $st -TimeZone $utc
    Assert-Equal 'schedule' $d.Reason
    Assert-Equal (D '2026-02-03T07:00:00Z') $d.UnlockAt
    $free = Get-KcEnforcementDecision -Now (D '2026-02-03T12:00:00Z') -State $st -TimeZone $utc
    Assert-Equal 'none' $free.Reason
}

Test 'dates from PS7-style [datetime] JSON values are handled' {
    Assert-Equal (D '2026-01-12T21:00:00Z') (ConvertTo-KcDate ([datetime]::SpecifyKind([datetime]'2026-01-12 21:00:00', 'Utc')))
    Assert-Equal (D '2026-01-12T21:00:00Z') (ConvertTo-KcDate '2026-01-12T21:00:00+00:00')
    Assert-Equal $null (ConvertTo-KcDate 'not a date')
}

# --- Countdown episodes ---------------------------------------------------------------------------------

Test 'schedule lock counts down, then locks; the deadline survives re-evaluation' {
    $local = New-KcLocalState
    $st = New-State
    $t0 = D '2026-01-12T21:00:10Z'
    $dec = Get-KcEnforcementDecision -Now $t0 -State $st
    $m = Update-KcLockEpisode -Decision $dec -Local $local -Now $t0 -CountdownSeconds 300
    Assert-Equal 'countdown' $m.Mode; Assert-Equal (D '2026-01-12T21:05:10Z') $m.CountdownEndsAt
    # Simulate an agent restart: reload local state from JSON, re-evaluate later.
    $local = (ConvertTo-Json $local -Depth 5) | ConvertFrom-Json
    $t1 = D '2026-01-12T21:04:00Z'
    $m = Update-KcLockEpisode -Decision (Get-KcEnforcementDecision -Now $t1 -State $st) -Local $local -Now $t1 -CountdownSeconds 300
    Assert-Equal 'countdown' $m.Mode; Assert-Equal (D '2026-01-12T21:05:10Z') $m.CountdownEndsAt
    $t2 = D '2026-01-12T21:05:10Z'
    $m = Update-KcLockEpisode -Decision (Get-KcEnforcementDecision -Now $t2 -State $st) -Local $local -Now $t2 -CountdownSeconds 300
    Assert-Equal 'locked' $m.Mode
}

Test 'immediate lock skips the countdown; unlocking clears the episode' {
    $local = New-KcLocalState
    $st = New-State @{ immediate_lock = @{ id = 'L2'; reason = $null; requested_at = '2026-01-12T12:00:00Z' } }
    $now = D '2026-01-12T12:00:05Z'
    $m = Update-KcLockEpisode -Decision (Get-KcEnforcementDecision -Now $now -State $st) -Local $local -Now $now -CountdownSeconds 300
    Assert-Equal 'locked' $m.Mode
    $m = Update-KcLockEpisode -Decision (Get-KcEnforcementDecision -Now $now -State (New-State)) -Local $local -Now $now -CountdownSeconds 300
    Assert-Equal 'none' $m.Mode; Assert-Equal $null $local.lock_episode
}

# --- PIN -------------------------------------------------------------------------------------------------

Test 'PIN hash from the server verifies (PBKDF2-SHA256 test vector)' {
    $pin = (New-State).pin
    Assert-Equal $true (Test-KcPin -Pin '4321' -PinConfig $pin)
    Assert-Equal $false (Test-KcPin -Pin '4322' -PinConfig $pin)
}

Test 'wrong PINs lead to lockout; lockout rejects even the right PIN' {
    $local = New-KcLocalState; $st = New-State; $now = D '2026-01-12T22:00:00Z'
    $r1 = Invoke-KcPinAttempt -Pin '0000' -State $st -Local $local -Now $now
    $r2 = Invoke-KcPinAttempt -Pin 'abcd' -State $st -Local $local -Now $now
    Assert-Equal $false $r1.Ok; Assert-Equal 2 $local.pin_failures
    $r3 = Invoke-KcPinAttempt -Pin '1111' -State $st -Local $local -Now $now
    Assert-True ($r3.Events | Where-Object { $_.type -eq 'pin.lockout' }) 'lockout event'
    Assert-Equal (D '2026-01-12T22:15:00Z') (ConvertTo-KcDate $local.pin_locked_until)
    $r4 = Invoke-KcPinAttempt -Pin '4321' -State $st -Local $local -Now $now.AddMinutes(5)
    Assert-Equal $false $r4.Ok
    $r5 = Invoke-KcPinAttempt -Pin '4321' -State $st -Local $local -Now $now.AddMinutes(16)
    Assert-Equal $true $r5.Ok
}

Test 'correct PIN grants a local override and releases an immediate lock' {
    $local = New-KcLocalState
    $st = New-State @{ immediate_lock = @{ id = 'L3'; reason = $null; requested_at = '2026-01-12T21:00:00Z' } }
    $now = D '2026-01-12T22:00:00Z'
    $dec = Get-KcEnforcementDecision -Now $now -State $st -Local $local
    $r = Invoke-KcPinAttempt -Pin '4321' -State $st -Local $local -Now $now -Decision $dec
    Assert-Equal $true $r.Ok
    Assert-Equal @('lock.released_by_pin', 'pin.override_granted') @($r.Events | ForEach-Object { $_.type })
    $after = Get-KcEnforcementDecision -Now $now.AddMinutes(30) -State $st -Local $local
    Assert-Equal 'override' $after.Reason
    $expired = Get-KcEnforcementDecision -Now $now.AddMinutes(61) -State $st -Local $local
    Assert-Equal 'schedule' $expired.Reason   # lock L3 stays released; the schedule applies again
}

Test 'no PIN configured' {
    $st = New-State @{ pin = @{ hash = $null; salt = $null; iterations = $null; max_attempts = 5; lockout_minutes = 15; override_minutes = 60 } }
    $r = Invoke-KcPinAttempt -Pin '1234' -State $st -Local (New-KcLocalState) -Now (D '2026-01-12T22:00:00Z')
    Assert-Equal $false $r.Ok; Assert-Match 'No PIN' $r.Message
}

# --- Files, queue, inbox, tamper --------------------------------------------------------------------------

Test 'JSON files are written atomically and read back' {
    $f = Join-Path $paths.Config 'x.json'
    Write-KcJsonFile -Path $f -Object @{ a = 1 }
    Write-KcJsonFile -Path $f -Object @{ a = 2; b = @(1, 2) }
    $o = Read-KcJsonFile -Path $f
    Assert-Equal 2 $o.a; Assert-Equal 2 @($o.b).Count
    Assert-Equal 0 @(Get-ChildItem $paths.Config -Filter '*.tmp').Count
}

Test 'local state round-trips and tolerates a corrupt file' {
    $l = New-KcLocalState; $l.pin_failures = 2; $l.released_locks = @('a')
    Write-KcJsonFile -Path $paths.LocalFile -Object $l
    $r = Read-KcLocalState
    Assert-Equal 2 $r.pin_failures; Assert-Equal @('a') @($r.released_locks)
    [IO.File]::WriteAllText($paths.LocalFile, '{nope')
    Assert-Equal 0 (Read-KcLocalState).pin_failures
}

Test 'event queue appends, trims and keeps valid JSON lines' {
    Add-KcEvent -Type 'agent.started' -Message 'hello'
    Add-KcEvent -Type 'tamper.detected' -Message 'bad' -Severity critical -Data @{ files = @('x') }
    $lines = Get-KcQueuedEvents
    Assert-Equal 2 $lines.Count
    $e = $lines[1] | ConvertFrom-Json
    Assert-Equal 'critical' $e.severity; Assert-Equal 'x' @($e.data.files)[0]
    Set-KcQueuedEvents -Lines @($lines | Select-Object -Skip 1)
    Assert-Equal 1 (Get-KcQueuedEvents).Count
    Set-KcQueuedEvents -Lines @()
    Assert-Equal 0 (Get-KcQueuedEvents).Count
}

Test 'inbox requests are consumed once; oversized ones are dropped' {
    $id = New-KcInboxRequest -Type 'pin_attempt' -Fields @{ pin = '1234' }
    [IO.File]::WriteAllText((Join-Path $paths.Inbox 'big.json'), ('{"type":"x","pad":"' + ('a' * 5000) + '"}'))
    $reqs = Get-KcInboxRequests
    Assert-Equal 1 $reqs.Count; Assert-Equal $id $reqs[0].request_id; Assert-Equal '1234' $reqs[0].pin
    Assert-Equal 0 (Get-KcInboxRequests).Count
    Assert-Equal 0 @(Get-ChildItem $paths.Inbox).Count
}

Test 'tamper check detects modified, missing and unexpected files' {
    $root = Join-Path $tmp 'install'
    [void][IO.Directory]::CreateDirectory((Join-Path $root 'Modules'))
    Set-Content -Path (Join-Path $root 'Watchdog.ps1') -Value 'a'
    Set-Content -Path (Join-Path $root 'Modules\M.psm1') -Value 'b'
    Write-KcJsonFile -Path $paths.Manifest -Object @{ files = (Get-KcFileHashes -InstallRoot $root) }
    Assert-Equal 0 (Test-KcTamper -InstallRoot $root).Count
    Set-Content -Path (Join-Path $root 'Watchdog.ps1') -Value 'evil'
    Remove-Item (Join-Path $root 'Modules\M.psm1')
    Set-Content -Path (Join-Path $root 'Extra.ps1') -Value 'c'
    $problems = Test-KcTamper -InstallRoot $root
    Assert-Equal 3 $problems.Count
    Assert-True ($problems -contains 'modified: Watchdog.ps1') 'modified'
}

Test 'device token round-trips through protected storage' {
    Save-KcDeviceToken -Token 'secret-token'
    Assert-Equal 'secret-token' (Get-KcDeviceToken)
    Assert-True ([IO.File]::ReadAllText($paths.TokenFile) -notmatch 'secret-token') 'not stored in clear text'
}

# --- Installer helpers --------------------------------------------------------------------------------------

Test 'installer settings come from install-config.json, explicit parameters win' {
    $cfg = '{"api_url":"https://kc.example.com","enrollment_code":"ABCD-EFGH-JKMN","expires_at":"2026-01-13T21:00:00+00:00","child_user":"Sam"}' | ConvertFrom-Json
    $r = Resolve-KcInstallSettings -Config $cfg
    Assert-Equal 'https://kc.example.com' $r.ApiUrl
    Assert-Equal 'ABCD-EFGH-JKMN' $r.EnrollmentCode
    Assert-Equal 'Sam' $r.ChildUser
    Assert-Equal (D '2026-01-13T21:00:00Z') $r.ExpiresAt
    Assert-Equal $false $r.AllowInsecureHttp
    Assert-Equal $true $r.FromConfig
    $o = Resolve-KcInstallSettings -Config $cfg -ChildUser 'Maya' -EnrollmentCode 'ZZZZ-ZZZZ-ZZZZ'
    Assert-Equal 'Maya' $o.ChildUser
    Assert-Equal 'ZZZZ-ZZZZ-ZZZZ' $o.EnrollmentCode
    Assert-Equal $null $o.ExpiresAt   # the config's expiry belongs to the config's code
}

Test 'installer settings without a config file pass parameters through' {
    $r = Resolve-KcInstallSettings -Config $null -ApiUrl 'http://localhost:8000' -AllowInsecureHttp
    Assert-Equal 'http://localhost:8000' $r.ApiUrl
    Assert-Equal $true $r.AllowInsecureHttp
    Assert-Equal $false $r.FromConfig
    $local = '{"api_url":"http://localhost:8000","enrollment_code":"X","allow_insecure_http":true}' | ConvertFrom-Json
    Assert-Equal $true (Resolve-KcInstallSettings -Config $local).AllowInsecureHttp
}

Test 'child account picker offers only enabled, standard, non-built-in accounts' {
    $base = 'S-1-5-21-1-2-3'
    $users = @(
        [pscustomobject]@{ Name = 'Administrator'; FullName = ''; Enabled = $false; SID = "$base-500" },
        [pscustomobject]@{ Name = 'Guest'; FullName = ''; Enabled = $true; SID = "$base-501" },
        [pscustomobject]@{ Name = 'WDAGUtilityAccount'; FullName = ''; Enabled = $true; SID = "$base-504" },
        [pscustomobject]@{ Name = 'Parent'; FullName = 'Mum'; Enabled = $true; SID = "$base-1001" },
        [pscustomobject]@{ Name = 'Sam'; FullName = 'Sam'; Enabled = $true; SID = "$base-1002" },
        [pscustomobject]@{ Name = 'OtherAdmin'; FullName = ''; Enabled = $true; SID = "$base-1003" },
        [pscustomobject]@{ Name = 'Old'; FullName = ''; Enabled = $false; SID = "$base-1004" },
        [pscustomobject]@{ Name = 'Maya'; FullName = 'Maya'; Enabled = $true; SID = "$base-1005" }
    )
    $c = Select-KcChildCandidates -Users $users -AdminSids @("$base-1003") -CurrentSid "$base-1001"
    Assert-Equal @('Maya', 'Sam') @($c | ForEach-Object { $_.Name })
    Assert-Equal 0 (Select-KcChildCandidates -Users @() -CurrentSid 'x').Count
}

Test 'Trusted mode also offers administrator accounts, never the parent running the installer' {
    $base = 'S-1-5-21-1-2-3'
    $users = @(
        [pscustomobject]@{ Name = 'Parent'; FullName = ''; Enabled = $true; SID = "$base-1001" },
        [pscustomobject]@{ Name = 'Sam'; FullName = ''; Enabled = $true; SID = "$base-1002" }
    )
    $c = Select-KcChildCandidates -Users $users -AdminSids @("$base-1001", "$base-1002") -CurrentSid "$base-1001" -IncludeAdmins
    Assert-Equal @('Sam') @($c | ForEach-Object { $_.Name })
}

Test 'install mode comes from the config, defaults to managed, and rejects unknown values' {
    Assert-Equal 'managed' (Resolve-KcInstallSettings -Config $null).Mode
    $cfg = '{"api_url":"https://kc.example.com","mode":"trusted"}' | ConvertFrom-Json
    Assert-Equal 'trusted' (Resolve-KcInstallSettings -Config $cfg).Mode
    Assert-Equal 'managed' (Resolve-KcInstallSettings -Config $cfg -Mode 'managed').Mode
    $bad = '{"mode":"anything"}' | ConvertFrom-Json
    Assert-Equal 'managed' (Resolve-KcInstallSettings -Config $bad).Mode
}

Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
Complete-Tests
