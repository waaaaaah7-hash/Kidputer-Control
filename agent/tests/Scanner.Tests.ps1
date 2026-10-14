<#
    Tests for the folder-size scanner (KidputerControl.Scanner.cs) and Scan.ps1. Off Windows the scanner
    lists folders with .NET instead of FindFirstFileEx, so the totals logic is tested everywhere.
    powershell -File agent\tests\Scanner.Tests.ps1
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TestHarness.ps1')
$agentRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
if (-not ('KcScanner' -as [type])) { Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $agentRoot 'KidputerControl/Modules/KidputerControl.Scanner.cs'))) }

$root = Join-Path ([IO.Path]::GetTempPath()) ("kc-scan-" + [guid]::NewGuid().ToString('N'))
function F([string]$Rel, [int]$Bytes) {
    $p = Join-Path $root $Rel
    [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($p))
    [IO.File]::WriteAllBytes($p, (New-Object byte[] $Bytes))
}
F 'Games/Big/data.pak' 300000
F 'Games/Big/game.exe' 50000
F 'Games/Small/save.sav' 100
F 'Downloads/movie.mp4' 200000
F 'Downloads/mods.zip' 120000
F 'Downloads/deep/a/b/c/notes.txt' 10
F 'readme.txt' 5
[void][IO.Directory]::CreateDirectory((Join-Path $root 'Empty'))

$outDir = Join-Path ([IO.Path]::GetTempPath()) ("kc-scanout-" + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($outDir)
$progress = Join-Path $outDir 'progress.json'
$json = [KcScanner]::ScanToJson($root, $null, 100000, 12)
$r = $json | ConvertFrom-Json

function Child($Node, [string]$Name) { @($Node.c) | Where-Object { $_.n -eq $Name } }

Test 'totals add up from files to folders to the root' {
    Assert-Equal 670115 $r.tree.s
    Assert-Equal 7 $r.tree.f
    $games = Child $r.tree 'Games'
    Assert-Equal 350100 $games.s
    Assert-Equal 3 $games.f
    Assert-Equal 2 $games.d
}

Test 'folders sort biggest first; small ones deeper down are folded into their parent' {
    Assert-Equal @('Games', 'Downloads', 'Empty') @(@($r.tree.c) | ForEach-Object { $_.n })
    $games = Child $r.tree 'Games'
    Assert-Equal @('Big', 'Small') @(@($games.c) | ForEach-Object { $_.n })     # second level: always kept
    $deep = Child (Child $r.tree 'Downloads') 'deep'
    Assert-Equal $null (@($deep.c) | Select-Object -First 1)                    # third level, small: folded
    Assert-Equal 1 $deep.f
}

Test 'size on disk rounds each file up to the cluster size' {
    $c = [long]$r.cluster
    Assert-True ($c -ge 512) 'cluster size known'
    Assert-True ([long]$r.tree.a -ge [long]$r.tree.s) 'allocated at least the size'
}

Test 'largest files and space by type' {
    Assert-Match 'data\.pak$' $r.largest[0].p
    Assert-Equal 300000 $r.largest[0].s
    Assert-Equal 7 @($r.largest).Count
    Assert-Equal 300100 $r.by_type.'game data'.s
    Assert-Equal 200000 $r.by_type.video.s
    Assert-Equal 120000 $r.by_type.archives.s
    Assert-Equal 50000 $r.by_type.programs.s
}

Test 'Scan.ps1 writes the result file and progress' {
    $out = Join-Path $outDir 'result.json'
    $ps = (Get-Process -Id $PID).Path
    & $ps -NoProfile -File (Join-Path $agentRoot 'KidputerControl/Scan.ps1') -Root (Join-Path $root 'Games') -OutFile $out -ProgressFile $progress -MinNodeBytes 1
    Assert-Equal 0 $LASTEXITCODE
    $res = [IO.File]::ReadAllText($out) | ConvertFrom-Json
    Assert-Equal 350100 $res.tree.s
    Assert-Equal $true ([IO.File]::ReadAllText($progress) | ConvertFrom-Json).done
}

if (Test-Path -LiteralPath '/bin/sh') {
    Test 'folder links are not followed, so nothing is counted twice' {
        $link = Join-Path $root 'Link'
        & ln -s (Join-Path $root 'Games') $link
        $again = [KcScanner]::ScanToJson($root, $null, 1, 12) | ConvertFrom-Json
        Assert-Equal 670115 $again.tree.s
        Remove-Item -LiteralPath $link -Force
    }
}

Remove-Item -Recurse -Force $root, $outDir -ErrorAction SilentlyContinue
Complete-Tests
