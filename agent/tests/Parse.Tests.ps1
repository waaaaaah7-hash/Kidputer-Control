<#
    Static checks for every agent script: parses cleanly, and the embedded C# compiles.
    Runs anywhere PowerShell runs (Windows-only APIs are compiled, not called).
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TestHarness.ps1')
$agentRoot = Resolve-Path (Join-Path $PSScriptRoot '..')

foreach ($file in Get-ChildItem -Path $agentRoot -Recurse -Include '*.ps1', '*.psm1') {
    Test "parses: $($file.FullName.Substring($agentRoot.Path.Length + 1))" {
        $tokens = $null; $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
        if ($errors.Count) { throw ($errors | ForEach-Object { "line $($_.Extent.StartLineNumber): $($_.Message)" }) -join '; ' }
    }
}

# Windows PowerShell 5.1 reads BOM-less scripts in the system code page, so a stray em dash or smart quote
# would be garbled (or break parsing) on the PC. Keep shipped files plain ASCII.
foreach ($file in Get-ChildItem -Path $agentRoot -Recurse -Include '*.ps1', '*.psm1', '*.cs') {
    Test "ASCII only: $($file.FullName.Substring($agentRoot.Path.Length + 1))" {
        $bytes = [IO.File]::ReadAllBytes($file.FullName)
        $bad = @($bytes | Where-Object { $_ -gt 127 }).Count
        if ($bad) { throw "$bad non-ASCII byte(s); Windows PowerShell 5.1 would misread them" }
    }
}

Test 'native interop compiles' {
    $cs = Join-Path $agentRoot 'KidputerControl\Modules\KidputerControl.Native.cs'
    if (-not ('KcNative' -as [type])) { Add-Type -TypeDefinition ([IO.File]::ReadAllText($cs)) }
    Assert-Equal ([uint32]4294967295) ([KcNative]::NoSession)
}

Test 'UI agent interop compiles' {
    $src = [IO.File]::ReadAllText((Join-Path $agentRoot 'KidputerControl/UiAgent.ps1'))
    $m = [regex]::Match($src, "(?s)Add-Type -Namespace KcUi -Name User32 -MemberDefinition @'\r?\n(.*?)\r?\n'@")
    Assert-True $m.Success 'found the KcUi definition'
    if (-not ('KcUiParseTest.User32' -as [type])) { Add-Type -Namespace KcUiParseTest -Name User32 -MemberDefinition $m.Groups[1].Value }
    Assert-True ([bool]('KcUiParseTest.User32' -as [type])) 'compiled'
}

Complete-Tests
