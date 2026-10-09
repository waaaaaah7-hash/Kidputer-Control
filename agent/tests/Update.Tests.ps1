<#
    Unit tests for KidputerControl.Update.psm1 (agent auto-update). Self-contained:
    powershell -File agent\tests\Update.Tests.ps1
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TestHarness.ps1')
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Common.psm1') -Force
Import-Module (Join-Path $PSScriptRoot '..\KidputerControl\Modules\KidputerControl.Update.psm1') -Force

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("kc-upd-" + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($tmp)

function New-Tree([string]$Root, [hashtable]$Files) {
    [void][IO.Directory]::CreateDirectory($Root)
    foreach ($k in $Files.Keys) {
        $p = ConvertTo-KcLocalPath -Root $Root -Relative $k
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($p))
        [IO.File]::WriteAllText($p, $Files[$k])
    }
}

Test 'bundle fingerprint matches the server (same test vector as server/tests/test_agent_update.py)' {
    $files = @(
        [pscustomobject]@{ path = 'Watchdog.ps1'; sha256 = ('ab' * 32) },
        [pscustomobject]@{ path = 'Modules\KidputerControl.Common.psm1'; sha256 = ('CD' * 32) }
    )
    Assert-Equal 'E7913FF9C10CBB3E6EFDCB0D902D65DF7BEF313D8B6D92C5A2F68483680F9723' (Get-KcBundleFingerprint -Files $files)
    # Order and path case don't matter.
    $files2 = @([pscustomobject]@{ path = 'MODULES/kidputercontrol.common.psm1'; sha256 = ('cd' * 32) }, $files[0])
    Assert-Equal (Get-KcBundleFingerprint -Files $files) (Get-KcBundleFingerprint -Files $files2)
}

Test 'manifest paths that try to leave the install folder are refused' {
    foreach ($bad in '..\Windows\x.ps1', 'Modules\..\..\x', 'C:\x.ps1', '', 'a\\b') {
        $threw = $false
        try { [void](ConvertTo-KcLocalPath -Root $tmp -Relative $bad) } catch { $threw = $true }
        Assert-True $threw "refused: '$bad'"
    }
    Assert-Match 'Modules.KidputerControl\.Apps\.psm1$' (ConvertTo-KcLocalPath -Root $tmp -Relative 'Modules\KidputerControl.Apps.psm1')
}

Test 'installing an update replaces changed files, adds new ones and removes old ones' {
    $install = Join-Path $tmp 'install1'
    $staging = Join-Path $tmp 'staging1'
    New-Tree $install @{ 'Watchdog.ps1' = 'old watchdog'; 'Modules\Old.psm1' = 'gone soon'; 'Modules\Common.psm1' = 'old common' }
    New-Tree $staging @{ 'Watchdog.ps1' = 'new watchdog'; 'Modules\Common.psm1' = 'new common'; 'Modules\Apps.psm1' = 'brand new' }
    Install-KcAgentUpdate -StagingDir $staging -InstallRoot $install -BackupDir (Join-Path $tmp 'backup1')
    Assert-Equal (Get-KcLocalBundle -Root $staging).Fingerprint (Get-KcLocalBundle -Root $install).Fingerprint
    Assert-Equal 'new watchdog' ([IO.File]::ReadAllText((Join-Path $install 'Watchdog.ps1')))
    Assert-Equal $false (Test-Path (ConvertTo-KcLocalPath -Root $install -Relative 'Modules\Old.psm1'))
    # The previous version is kept as a backup.
    Assert-Equal 'old watchdog' ([IO.File]::ReadAllText((Join-Path (Join-Path $tmp 'backup1') 'Watchdog.ps1')))
}

Test 'a failed update puts the previous files back' {
    $install = Join-Path $tmp 'install2'
    New-Tree $install @{ 'Watchdog.ps1' = 'working version'; 'Modules\Common.psm1' = 'common' }
    $before = (Get-KcLocalBundle -Root $install).Fingerprint
    $threw = $false
    try { Install-KcAgentUpdate -StagingDir (Join-Path $tmp 'does-not-exist') -InstallRoot $install -BackupDir (Join-Path $tmp 'backup2') } catch { $threw = $true; Assert-Match 'rolled back' $_.Exception.Message }
    Assert-True $threw 'update failed'
    Assert-Equal $before (Get-KcLocalBundle -Root $install).Fingerprint
}

Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
Complete-Tests
