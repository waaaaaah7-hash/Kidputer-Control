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

Export-ModuleMember -Function *-Kc*
