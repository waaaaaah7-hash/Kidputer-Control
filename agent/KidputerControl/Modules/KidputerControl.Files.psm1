#Requires -Version 5.1
<#
    Drives, folder sizes and the file explorer (roadmap #19). Names, sizes and dates only: nothing here
    opens or reads a file. The heavy scan itself is KidputerControl.Scanner.cs, run by Scan.ps1 in a
    separate background-priority process. Windows PowerShell 5.1 compatible, plain ASCII.
#>

Set-StrictMode -Version 3.0

function Get-KcDriveReport {
    <# Every ready drive with size and free space, plus phones and cameras connected by USB (by name only). #>
    $drives = @([IO.DriveInfo]::GetDrives() | Where-Object { $_.IsReady } | ForEach-Object {
            [ordered]@{
                name   = $_.Name
                label  = [string]$_.VolumeLabel
                type   = [string]$_.DriveType
                format = [string]$_.DriveFormat
                size   = [long]$_.TotalSize
                free   = [long]$_.AvailableFreeSpace
            }
        })
    $devices = @()
    try {
        $devices = @(Get-PnpDevice -Class WPD -PresentOnly -ErrorAction Stop | ForEach-Object { [string]$_.FriendlyName } | Where-Object { $_ } | Sort-Object -Unique)
    } catch { }
    @{ drives = $drives; devices = $devices }
}

function Test-KcFolderPath {
    <# A full path on a local drive (C:\...), without ".." tricks or network/device paths. #>
    param([string]$Path)
    if (-not $Path -or $Path.Length -gt 1024) { return $false }
    if ($Path -notmatch '^[A-Za-z]:\\') { return $false }
    foreach ($part in $Path.Split('\')) { if ($part -eq '..') { return $false } }
    $true
}

function Get-KcFolderListing {
    <#
        One folder's contents for the dashboard's explorer: folders first, then files, by name. Each entry:
        n (name), dir, s (size, files only), m (modified, ISO), h (hidden or system), l (a link to
        elsewhere). At most $Max entries.
    #>
    param([Parameter(Mandatory)][string]$Path, [int]$Max = 3000)
    $dir = New-Object IO.DirectoryInfo($Path)
    if (-not $dir.Exists) { throw "That folder doesn't exist (any more)." }
    $entries = New-Object System.Collections.ArrayList
    $truncated = $false
    $count = 0
    try {
        foreach ($fi in $dir.EnumerateFileSystemInfos()) {
            $count++
            if ($entries.Count -ge $Max) { $truncated = $true; continue }
            $attrs = $fi.Attributes
            $isDir = ($attrs -band [IO.FileAttributes]::Directory) -ne 0
            $e = [ordered]@{
                n   = $fi.Name
                dir = $isDir
                m   = (ConvertTo-KcIso ([DateTimeOffset]$fi.LastWriteTimeUtc))
                h   = (($attrs -band ([IO.FileAttributes]::Hidden -bor [IO.FileAttributes]::System)) -ne 0)
                l   = (($attrs -band [IO.FileAttributes]::ReparsePoint) -ne 0)
            }
            if (-not $isDir) { $e.s = [long]$fi.Length }
            [void]$entries.Add($e)
        }
    } catch [UnauthorizedAccessException] {
        throw "Windows doesn't allow reading that folder."
    }
    $sorted = @($entries | Sort-Object @{ Expression = { -not $_.dir } }, @{ Expression = { $_.n } })
    @{ path = $dir.FullName; entries = $sorted; total = $count; truncated = $truncated }
}

function Get-KcScanMinBytes {
    <# Folders smaller than this (below the top levels) are folded into their parent: 0.2% of the used space, at least 50 MB. #>
    param([long]$UsedBytes)
    [long][Math]::Max(50MB, [Math]::Floor($UsedBytes * 0.002))
}

function Get-KcDueScan {
    <#
        Which fixed drive is due for its regular folder-size scan, most overdue first, or $null.
        $LastScans: letter -> ISO time of the last finished scan.
    #>
    param([object[]]$Drives, $LastScans, [int]$EveryDays, [Parameter(Mandatory)][DateTimeOffset]$Now)
    if ($EveryDays -le 0) { return $null }
    $best = $null
    $bestAge = -1.0
    foreach ($d in @($Drives)) {
        if ([string](Get-KcProp ([pscustomobject]$d) 'type' '') -ne 'Fixed') { continue }
        $letter = ([string](Get-KcProp ([pscustomobject]$d) 'name' '')).Substring(0, 1).ToUpperInvariant()
        $last = $null
        if ($LastScans -is [Collections.IDictionary]) { if ($LastScans.Contains($letter)) { $last = ConvertTo-KcDate $LastScans[$letter] } }
        elseif ($null -ne $LastScans) { $last = ConvertTo-KcDate (Get-KcProp $LastScans $letter) }
        $age = [double]::MaxValue
        if ($last) { $age = ($Now - $last).TotalDays }
        if ($age -ge $EveryDays -and $age -gt $bestAge) { $best = $letter; $bestAge = $age }
    }
    $best
}

Export-ModuleMember -Function *-Kc*
