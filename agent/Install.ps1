#Requires -Version 5.1
<#
.SYNOPSIS
    Install (or upgrade) the Session Monitor agent on the child's PC. Run from an elevated PowerShell.

.DESCRIPTION
    1. Copies the agent to %ProgramFiles%\SessionMonitor (Users: read/execute only).
    2. Creates %ProgramData%\SessionMonitor with locked-down ACLs:
         config\, logs\   SYSTEM + Administrators only (device token, cached state, PIN hash, event queue)
         ipc\             SYSTEM writes, Users read (what the lock screen should show)
         ipc\inbox\       Users may only drop files in (PIN attempts, message acks), not list or read them
    3. Enrolls with the cloud API using the one-time code from the dashboard and stores the device
       token (DPAPI, machine scope).
    4. Records a SHA-256 manifest of the installed files for tamper detection.
    5. Registers the SYSTEM watchdog scheduled task (\SessionMonitor\Watchdog) and starts it.

.EXAMPLE
    .\Install.ps1 -ApiUrl https://monitor.example.com -EnrollmentCode ABCD-EFGH-JKMN -ChildUser Sam

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
    # Install even if the child account is an administrator (enforcement could then be disabled by the child).
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated (Run as administrator) PowerShell.'
}

$source = Join-Path $PSScriptRoot 'SessionMonitor'
$installRoot = Join-Path $env:ProgramFiles 'SessionMonitor'
$dataRoot = Join-Path $env:ProgramData 'SessionMonitor'
$taskPath = '\SessionMonitor\'
$taskName = 'Watchdog'

Import-Module (Join-Path $source 'Modules\SessionMonitor.Common.psm1') -Force
$paths = Initialize-SmPaths -DataRoot $dataRoot

# --- Inputs ----------------------------------------------------------------------------------------------------
$existing = $null
try { $existing = Read-SmJsonFile -Path $paths.AgentFile } catch { }
$isUpgrade = ($null -ne $existing) -and [IO.File]::Exists($paths.TokenFile) -and -not $EnrollmentCode

if (-not $isUpgrade) {
    if (-not $ApiUrl) { $ApiUrl = Read-Host 'Cloud API URL (e.g. https://monitor.example.com)' }
    if (-not $EnrollmentCode) { $EnrollmentCode = Read-Host 'Enrollment code from the dashboard' }
    if (-not $ChildUser) { $ChildUser = Read-Host "Child's Windows account name" }
} else {
    if (-not $ApiUrl) { $ApiUrl = [string]$existing.api_url }
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
if ($childIsAdmin -and -not $Force) {
    throw "'$ChildUser' is an administrator and could simply remove the agent. Make it a standard user (recommended) or pass -Force."
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
    $enrolled = Invoke-SmEnroll -BaseUrl $ApiUrl -EnrollmentCode $EnrollmentCode
    Save-SmDeviceToken -Token ([string]$enrolled.device_token)
    $deviceId = [string]$enrolled.device_id
    # A fresh enrollment starts from clean state.
    foreach ($f in $paths.StateFile, $paths.LocalFile, $paths.QueueFile) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
} else {
    $deviceId = [string]$existing.device_id
}
Write-SmJsonFile -Path $paths.AgentFile -Object ([ordered]@{
        api_url        = $ApiUrl
        device_id      = $deviceId
        child_user_sid = $childSid
        agent_version  = (Get-SmAgentVersion)
        installed_at   = (ConvertTo-SmIso (Get-SmNow))
    })

# Fetch the configuration now so enforcement is ready before the first watchdog sync.
try {
    $r = Sync-SmState -BaseUrl $ApiUrl -Token (Get-SmDeviceToken) -CachedState $null
    if ($r.Changed) { Write-Host "Configuration received for '$($r.State.name)'." } else { Write-Host 'Could not fetch configuration yet; the watchdog will retry.' }
} catch { Write-Host "Could not fetch configuration yet ($($_.Exception.Message)); the watchdog will retry." }

Write-SmJsonFile -Path $paths.Manifest -Object ([ordered]@{ created_at = (ConvertTo-SmIso (Get-SmNow)); files = (Get-SmFileHashes -InstallRoot $installRoot) })
Add-SmEvent -Type 'agent.installed' -Message "Agent $(Get-SmAgentVersion) installed (upgrade: $isUpgrade) for account SID $childSid"

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
    -Description 'Session Monitor watchdog: enforces the screen-time schedule. Removal requires an administrator.' -Force | Out-Null
Enable-ScheduledTask -TaskPath $taskPath -TaskName $taskName | Out-Null
Start-ScheduledTask -TaskPath $taskPath -TaskName $taskName

Write-Host ''
Write-Host 'Session Monitor is installed and running.' -ForegroundColor Green
Write-Host "  Device:   $deviceId"
Write-Host "  Enforced: $ChildUser ($childSid)"
Write-Host '  Set the schedule and lock-screen PIN from the dashboard.'
