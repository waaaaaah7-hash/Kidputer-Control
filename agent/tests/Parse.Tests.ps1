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

Test 'native interop compiles' {
    $cs = Join-Path $agentRoot 'KidputerControl\Modules\KidputerControl.Native.cs'
    if (-not ('KcNative' -as [type])) { Add-Type -TypeDefinition ([IO.File]::ReadAllText($cs)) }
    Assert-Equal ([uint32]4294967295) ([KcNative]::NoSession)
}

Complete-Tests
