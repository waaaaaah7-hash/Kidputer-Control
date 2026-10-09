#Requires -Version 5.1
<#
.SYNOPSIS
    Install (or upgrade) the Kidputer Control agent on the child's PC. Run from an elevated PowerShell.

.DESCRIPTION
    1. Copies the agent to %ProgramFiles%\KidputerControl (Users: read/execute only).
    2. Creates %ProgramData%\KidputerControl with locked-down ACLs:
         config\, logs\   SYSTEM + Administrators only (device token, cached state, PIN hash, event queue)
         ipc\             SYSTEM writes, Users read (what the lock screen should show)
         ipc\inbox\       Users may only drop files in (PIN attempts, message acks), not list or read them
    3. Enrolls with the cloud API using the one-time code from the dashboard and stores the device
       token (DPAPI, machine scope).
    4. Records a SHA-256 manifest of the installed files for tamper detection.
    5. Registers the SYSTEM watchdog scheduled task (\KidputerControl\Watchdog) and starts it.

.EXAMPLE
    # One-click installer from the dashboard: just double-click "Install Kidputer Control.cmd".
    # It runs this script, which reads install-config.json from its own folder.

.EXAMPLE
    .\Install.ps1 -ApiUrl https://monitor.example.com -EnrollmentCode ABCD-EFGH-JKMN -ChildUser Sam

.EXAMPLE
    # Trusted mode: the child's account is an administrator (they can install apps themselves).
    .\Install.ps1 -ApiUrl https://monitor.example.com -EnrollmentCode ABCD-EFGH-JKMN -ChildUser Sam -Mode trusted

.EXAMPLE
    # Upgrade in place, keeping the existing enrollment:
    .\Install.ps1
#>
[CmdletBinding()]
param(
    [string]$ApiUrl,
    [string]$EnrollmentCode,
    # Local account name, DOMAIN\name, or MicrosoftAccount\email of the child whose sessions are enforced.
    [string]$ChildUser,
    # Allow plain http:// (local testing only).
    [switch]$AllowInsecureHttp,
    # 'managed' (default): the child must have a standard account. 'trusted': the child is an administrator;
    # enforcement still works day to day, but the child could switch it off, so parents rely on notifications.
    [ValidateSet('', 'managed', 'trusted')]
    [string]$Mode = '',
    # Same as -Mode trusted for an administrator child (kept for older instructions).
    [switch]$Force,
    # Settings written by the dashboard's one-click download. Explicit parameters take precedence.
    [string]$ConfigFile = (Join-Path $PSScriptRoot 'install-config.json')
)

$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated (Run as administrator) PowerShell.'
}

$source = Join-Path $PSScriptRoot 'KidputerControl'
$installRoot = Join-Path $env:ProgramFiles 'KidputerControl'
$dataRoot = Join-Path $env:ProgramData 'KidputerControl'
$taskPath = '\KidputerControl\'
$taskName = 'Watchdog'

Import-Module (Join-Path $source 'Modules\KidputerControl.Common.psm1') -Force
$paths = Initialize-KcPaths -DataRoot $dataRoot

# --- Inputs ----------------------------------------------------------------------------------------------------
$config = $null
if ($ConfigFile -and (Test-Path -LiteralPath $ConfigFile)) {
    try { $config = Read-KcJsonFile -Path $ConfigFile } catch { throw "Could not read $ConfigFile. Download a new installer from the dashboard." }
}
$resolved = Resolve-KcInstallSettings -Config $config -ApiUrl $ApiUrl -EnrollmentCode $EnrollmentCode -ChildUser $ChildUser -AllowInsecureHttp:$AllowInsecureHttp -Mode $Mode
$ApiUrl = $resolved.ApiUrl
$EnrollmentCode = $resolved.EnrollmentCode
$ChildUser = $resolved.ChildUser
$AllowInsecureHttp = [switch]$resolved.AllowInsecureHttp
$Mode = $resolved.Mode
if ($Force) { $Mode = 'trusted' }
if ($resolved.ExpiresAt -and $resolved.ExpiresAt -le (Get-KcNow)) {
    throw "This installer's enrollment code expired at $($resolved.ExpiresAt.ToLocalTime().ToString('g')). Download a new installer from the dashboard."
}

function Read-ChildAccount {
    # Offer the PC's accounts by number, so nobody has to know the exact account name. Managed mode lists
    # standard accounts only; Trusted mode lists administrators too.
    $adminSids = @()
    try { $adminSids = @(Get-LocalGroupMember -SID 'S-1-5-32-544' | Where-Object { $_.SID } | ForEach-Object { $_.SID.Value }) } catch { }
    $users = @(Get-LocalUser | ForEach-Object { [pscustomobject]@{ Name = $_.Name; FullName = $_.FullName; Enabled = $_.Enabled; SID = $_.SID.Value } })
    $candidates = Select-KcChildCandidates -Users $users -AdminSids $adminSids -CurrentSid ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value) -IncludeAdmins:($Mode -eq 'trusted')
    if ($candidates.Count -eq 0) {
        if ($Mode -eq 'trusted') { Write-Host 'No other accounts were found on this PC.' -ForegroundColor Yellow }
        else { Write-Host 'No standard (non-administrator) accounts were found on this PC. If your child uses an administrator account, download a Trusted-mode installer instead.' -ForegroundColor Yellow }
        return Read-Host "Type the child's Windows account name"
    }
    Write-Host ''
    Write-Host "Which account does your child use?"
    for ($i = 0; $i -lt $candidates.Count; $i++) {
        $label = $candidates[$i].Name
        if ($candidates[$i].FullName -and $candidates[$i].FullName -ne $label) { $label = "$label ($($candidates[$i].FullName))" }
        Write-Host ("  {0}. {1}" -f ($i + 1), $label)
    }
    Write-Host '  0. Another account (type its name)'
    while ($true) {
        $choice = Read-Host 'Enter a number'
        $n = 0
        if ([int]::TryParse($choice, [ref]$n)) {
            if ($n -eq 0) { return Read-Host "Type the child's Windows account name" }
            if ($n -ge 1 -and $n -le $candidates.Count) { return $candidates[$n - 1].Name }
        }
        Write-Host "Please enter a number from 0 to $($candidates.Count)."
    }
}

$existing = $null
try { $existing = Read-KcJsonFile -Path $paths.AgentFile } catch { }
$isUpgrade = ($null -ne $existing) -and [IO.File]::Exists($paths.TokenFile) -and -not $EnrollmentCode

if (-not $isUpgrade) {
    if (-not $ApiUrl) { $ApiUrl = Read-Host 'Cloud API URL (e.g. https://monitor.example.com)' }
    if (-not $EnrollmentCode) { $EnrollmentCode = Read-Host 'Enrollment code from the dashboard' }
    if (-not $ChildUser) { $ChildUser = Read-ChildAccount }
} else {
    if (-not $ApiUrl) { $ApiUrl = [string]$existing.api_url }
    if (-not $PSBoundParameters.ContainsKey('Mode') -and -not $config -and -not $Force) { $Mode = [string](Get-KcProp $existing 'mode' 'managed') }
    Write-Host "Existing enrollment found (device $($existing.device_id)); upgrading in place."
}
$ApiUrl = $ApiUrl.TrimEnd('/')
if ($ApiUrl -notmatch '^https://' -and -not ($AllowInsecureHttp -and $ApiUrl -match '^http://')) {
    throw 'ApiUrl must start with https:// (use -AllowInsecureHttp only for local testing).'
}

if ($ChildUser) {
    try {
        $childSid = (New-Object Security.Principal.NTAccount($ChildUser)).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        throw "Windows account '$ChildUser' not found. Use the local account name, or MicrosoftAccount\<email> for a Microsoft account."
    }
} elseif ($isUpgrade) {
    $childSid = [string]$existing.child_user_sid
} else {
    throw 'ChildUser is required.'
}
if ($childSid -eq [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
    throw 'ChildUser is the account running this installer. Choose the child''s own (standard) account.'
}
$childIsAdmin = $false
try { $childIsAdmin = [bool](Get-LocalGroupMember -SID 'S-1-5-32-544' | Where-Object { $_.SID -and $_.SID.Value -eq $childSid }) } catch { }
if ($childIsAdmin -and $Mode -ne 'trusted') {
    throw "'$ChildUser' is an administrator and could simply remove the agent. Make it a standard user (recommended), or download a Trusted-mode installer from the dashboard (or pass -Mode trusted)."
}
if ($Mode -eq 'trusted') {
    Write-Host 'Trusted mode: the schedule is enforced, but an administrator child could switch it off. You will be notified if the PC goes silent.' -ForegroundColor Yellow
}

# --- Stop any running instance -------------------------------------------------------------------------------------
function Stop-Agent {
    $task = Get-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue
        Disable-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null
    }
    Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
        Where-Object { $_.CommandLine -and ($_.CommandLine.Contains("$installRoot\Watchdog.ps1") -or $_.CommandLine.Contains("$installRoot\UiAgent.ps1")) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
Stop-Agent

# --- Files & ACLs ---------------------------------------------------------------------------------------------------
$SYSTEM = '*S-1-5-18'; $ADMINS = '*S-1-5-32-544'; $USERS = '*S-1-5-32-545'; $CREATOR_OWNER = '*S-1-3-0'

function Set-IcaclsAcl {
    <# Replace the folder's ACL (and its contents') with exactly these grants. SIDs keep it language-independent. #>
    param([string]$Path, [string[]]$Grants)
    $icaclsArgs = @($Path, '/inheritance:r', '/grant:r') + $Grants + @('/T', '/Q')
    & icacls.exe @icaclsArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls failed on $Path" }
}

foreach ($d in $installRoot, $paths.DataRoot, $paths.Config, $paths.Logs, $paths.Ipc, $paths.Inbox) {
    [void][IO.Directory]::CreateDirectory($d)
}

Write-Host "Copying agent to $installRoot"
Get-ChildItem -Path $installRoot -Force | Remove-Item -Recurse -Force
Copy-Item -Path (Join-Path $source '*') -Destination $installRoot -Recurse -Force
Get-ChildItem -Path $installRoot -Recurse -File | Unblock-File

Set-IcaclsAcl $installRoot @("${SYSTEM}:(OI)(CI)F", "${ADMINS}:(OI)(CI)F", "${USERS}:(OI)(CI)RX")
Set-IcaclsAcl $paths.DataRoot @("${SYSTEM}:(OI)(CI)F", "${ADMINS}:(OI)(CI)F")
Set-IcaclsAcl $paths.Config @("${SYSTEM}:(OI)(CI)F", "${ADMINS}:(OI)(CI)F")
Set-IcaclsAcl $paths.Logs @("${SYSTEM}:(OI)(CI)F", "${ADMINS}:(OI)(CI)F")
Set-IcaclsAcl $paths.Ipc @("${SYSTEM}:(OI)(CI)F", "${ADMINS}:(OI)(CI)F", "${USERS}:(OI)(CI)RX")
# Inbox: Users can create files here (WD = add file) but not list the folder or read anyone's requests;
# the creator can write to the file it just created.
Set-IcaclsAcl $paths.Inbox @("${SYSTEM}:(OI)(CI)F", "${ADMINS}:(OI)(CI)F", "${USERS}:(WD,S)", "${CREATOR_OWNER}:(OI)(IO)(W)")

# --- Enrollment -------------------------------------------------------------------------------------------------------
if (-not $isUpgrade) {
    Write-Host "Enrolling with $ApiUrl"
    try {
        $enrolled = Invoke-KcEnroll -BaseUrl $ApiUrl -EnrollmentCode $EnrollmentCode
    } catch {
        if ($_.Exception.Message -match 'HTTP 401') {
            throw 'The enrollment code was rejected: it has expired or was already used. Download a new installer (or code) from the dashboard.'
        }
        throw "Could not reach $ApiUrl to enroll this PC: $($_.Exception.Message)"
    }
    Save-KcDeviceToken -Token ([string]$enrolled.device_token)
    $deviceId = [string]$enrolled.device_id
    # A fresh enrollment starts from clean state.
    foreach ($f in $paths.StateFile, $paths.LocalFile, $paths.QueueFile) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
} else {
    $deviceId = [string]$existing.device_id
}
Write-KcJsonFile -Path $paths.AgentFile -Object ([ordered]@{
        api_url        = $ApiUrl
        device_id      = $deviceId
        child_user_sid = $childSid
        mode           = $Mode
        agent_version  = (Get-KcAgentVersion)
        installed_at   = (ConvertTo-KcIso (Get-KcNow))
    })

# Fetch the configuration now so enforcement is ready before the first watchdog sync.
try {
    $r = Sync-KcState -BaseUrl $ApiUrl -Token (Get-KcDeviceToken) -CachedState $null
    if ($r.Changed) { Write-Host "Configuration received for '$($r.State.name)'." } else { Write-Host 'Could not fetch configuration yet; the watchdog will retry.' }
} catch { Write-Host "Could not fetch configuration yet ($($_.Exception.Message)); the watchdog will retry." }

Write-KcJsonFile -Path $paths.Manifest -Object ([ordered]@{ created_at = (ConvertTo-KcIso (Get-KcNow)); files = (Get-KcFileHashes -InstallRoot $installRoot) })
Add-KcEvent -Type 'agent.installed' -Message "Agent $(Get-KcAgentVersion) installed (upgrade: $isUpgrade) for account SID $childSid"

# --- Scheduled task -------------------------------------------------------------------------------------------------------
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $ps -WorkingDirectory $installRoot `
    -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f (Join-Path $installRoot 'Watchdog.ps1'))
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    # Backstop: re-launch every minute if it ever exits (a mutex keeps a single instance).
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1))
)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -Hidden
Register-ScheduledTask -TaskPath $taskPath -TaskName $taskName -Action $action -Trigger $triggers -Principal $taskPrincipal -Settings $settings `
    -Description 'Kidputer Control watchdog: enforces the screen-time schedule. Removal requires an administrator.' -Force | Out-Null
Enable-ScheduledTask -TaskPath $taskPath -TaskName $taskName | Out-Null
Start-ScheduledTask -TaskPath $taskPath -TaskName $taskName

Write-Host ''
Write-Host 'Kidputer Control is installed and running.' -ForegroundColor Green
Write-Host "  Device:   $deviceId"
Write-Host "  Enforced: $ChildUser ($childSid)"
Write-Host "  Mode:     $Mode"
Write-Host '  Set the schedule and lock-screen PIN from the dashboard.'
