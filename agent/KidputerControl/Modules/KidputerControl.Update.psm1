#Requires -Version 5.1
<#
    Agent auto-update (roadmap #13). The server says which agent bundle (a fingerprint over every file's
    SHA-256) this PC should run. When it differs from what's installed, the watchdog downloads the
    manifest and each file over HTTPS, checks every hash and the fingerprint, backs up the current
    files, swaps them in, and rolls back if anything fails. Windows PowerShell 5.1 compatible, ASCII.
#>

Set-StrictMode -Version 3.0

function Get-KcBundleFingerprint {
    <#
        SHA-256 over "path:sha256" lines, sorted by lower-cased path, paths with backslashes.
        Must match server/app/agent_bundle.py fingerprint().
    #>
    param([object[]]$Files)
    $lines = @($Files | ForEach-Object { [pscustomobject]@{ p = ([string]$_.path).Replace('/', '\').ToLowerInvariant(); h = ([string]$_.sha256).ToUpperInvariant() } } |
            Sort-Object { $_.p } | ForEach-Object { "$($_.p):$($_.h)`n" })
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes(($lines -join ''))
    $sha = [Security.Cryptography.SHA256]::Create()
    try { [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '') } finally { $sha.Dispose() }
}

function Get-KcFileSha256 {
    param([Parameter(Mandatory)][string]$Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') } finally { $stream.Dispose(); $sha.Dispose() }
}

function ConvertTo-KcLocalPath {
    <# Manifest paths use backslashes; turn one into a path under $Root, refusing anything that escapes it. #>
    param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][string]$Relative)
    $parts = $Relative -split '[\\/]'
    foreach ($p in $parts) {
        if (-not $p -or $p -eq '.' -or $p -eq '..' -or $p.Contains(':')) { throw "Unsafe path in update manifest: $Relative" }
    }
    $full = $Root
    foreach ($p in $parts) { $full = Join-Path $full $p }
    $full
}

function Get-KcLocalBundle {
    <# Every file under the install folder, with hashes, and the bundle fingerprint. #>
    param([Parameter(Mandatory)][string]$Root)
    $rootFull = (Resolve-Path -LiteralPath $Root).ProviderPath.TrimEnd('\', '/')
    $files = @(Get-ChildItem -LiteralPath $rootFull -Recurse -File | Sort-Object FullName | ForEach-Object {
            [pscustomobject]@{ path = $_.FullName.Substring($rootFull.Length + 1).Replace('/', '\'); sha256 = (Get-KcFileSha256 -Path $_.FullName) }
        })
    @{ Files = $files; Fingerprint = (Get-KcBundleFingerprint -Files $files) }
}

function Save-KcApiFile {
    <# Download a file from the API (device token auth) to $Destination. Throws on failure. #>
    param([Parameter(Mandatory)][string]$BaseUrl, [Parameter(Mandatory)][string]$Token, [Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Destination)
    $client = Get-KcHttpClient
    $req = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Get, ($BaseUrl.TrimEnd('/') + $Path))
    $req.Headers.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Bearer', $Token)
    try {
        $resp = $client.SendAsync($req).GetAwaiter().GetResult()
        if (-not $resp.IsSuccessStatusCode) { throw "HTTP $([int]$resp.StatusCode) for $Path" }
        $bytes = $resp.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
        [IO.File]::WriteAllBytes($Destination, $bytes)
    } finally { $req.Dispose() }
}

function Receive-KcAgentUpdate {
    <#
        Download bundle $Expected into $StagingRoot and verify it. Returns the staging folder.
        Throws if the manifest isn't the expected bundle or any file's hash is wrong.
    #>
    param([Parameter(Mandatory)][string]$BaseUrl, [Parameter(Mandatory)][string]$Token, [Parameter(Mandatory)][string]$Expected, [Parameter(Mandatory)][string]$StagingRoot)
    $r = Invoke-KcApi -BaseUrl $BaseUrl -Method GET -Path '/api/v1/agent/update/manifest' -Token $Token
    if ($r.Status -ne 200 -or $null -eq $r.Body) { throw "Update manifest unavailable (HTTP $($r.Status))" }
    $manifest = $r.Body
    $files = @($manifest.files)
    if ($files.Count -eq 0) { throw 'Update manifest lists no files' }
    $fp = Get-KcBundleFingerprint -Files $files
    if ($fp -ne $Expected.ToUpperInvariant() -or $fp -ne ([string]$manifest.bundle).ToUpperInvariant()) {
        throw "Update manifest doesn't match the expected bundle ($fp)"
    }
    $dir = Join-Path $StagingRoot $fp.Substring(0, 16)
    if ([IO.Directory]::Exists($dir)) { Remove-Item -LiteralPath $dir -Recurse -Force }
    [void][IO.Directory]::CreateDirectory($dir)
    foreach ($f in $files) {
        $dest = ConvertTo-KcLocalPath -Root $dir -Relative ([string]$f.path)
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($dest))
        Save-KcApiFile -BaseUrl $BaseUrl -Token $Token -Path ('/api/v1/agent/update/file?path=' + [Uri]::EscapeDataString([string]$f.path)) -Destination $dest
        $got = Get-KcFileSha256 -Path $dest
        if ($got -ne ([string]$f.sha256).ToUpperInvariant()) { throw "Downloaded $($f.path) doesn't match its fingerprint" }
    }
    $local = Get-KcLocalBundle -Root $dir
    if ($local.Fingerprint -ne $fp) { throw 'Staged update is incomplete' }
    $dir
}

function Install-KcAgentUpdate {
    <#
        Replace the files in $InstallRoot with the verified files in $StagingDir. The current files
        are copied to $BackupDir first and put back if anything goes wrong.
    #>
    param([Parameter(Mandatory)][string]$StagingDir, [Parameter(Mandatory)][string]$InstallRoot, [Parameter(Mandatory)][string]$BackupDir)
    if ([IO.Directory]::Exists($BackupDir)) { Remove-Item -LiteralPath $BackupDir -Recurse -Force }
    [void][IO.Directory]::CreateDirectory($BackupDir)
    Copy-Item -Path (Join-Path $InstallRoot '*') -Destination $BackupDir -Recurse -Force -ErrorAction Stop
    try {
        # Overwrite in place (never an empty folder, even if the power goes), then drop leftover files.
        Copy-Item -Path (Join-Path $StagingDir '*') -Destination $InstallRoot -Recurse -Force -ErrorAction Stop
        $staged = Get-KcLocalBundle -Root $StagingDir
        $keep = @{}
        foreach ($f in $staged.Files) { $keep[$f.path.ToLowerInvariant()] = $true }
        $rootFull = (Resolve-Path -LiteralPath $InstallRoot).ProviderPath.TrimEnd('\', '/')
        foreach ($old in @(Get-ChildItem -LiteralPath $rootFull -Recurse -File)) {
            $rel = $old.FullName.Substring($rootFull.Length + 1).Replace('/', '\').ToLowerInvariant()
            if (-not $keep.ContainsKey($rel)) { Remove-Item -LiteralPath $old.FullName -Force -ErrorAction Stop }
        }
        $now = Get-KcLocalBundle -Root $InstallRoot
        if ($now.Fingerprint -ne $staged.Fingerprint) { throw 'Installed files differ from the update' }
    } catch {
        $err = $_.Exception.Message
        Get-ChildItem -LiteralPath $InstallRoot -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item -Path (Join-Path $BackupDir '*') -Destination $InstallRoot -Recurse -Force -ErrorAction Stop
        throw "Update failed and was rolled back: $err"
    }
}

Export-ModuleMember -Function *-Kc*
