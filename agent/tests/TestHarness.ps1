# Minimal assertion harness (PowerShell Gallery isn't always reachable, and Windows ships an old Pester 3).
$script:Passed = 0
$script:Failed = New-Object System.Collections.ArrayList

function Test([string]$Name, [scriptblock]$Body) {
    try {
        & $Body
        $script:Passed++
        Write-Host "  [pass] $Name" -ForegroundColor Green
    } catch {
        [void]$script:Failed.Add($Name)
        Write-Host "  [FAIL] $Name`n         $($_.Exception.Message)`n         $($_.InvocationInfo.PositionMessage -replace "`n", "`n         ")" -ForegroundColor Red
    }
}

function Format-Value($v) {
    if ($null -eq $v) { return '$null' }
    if ($v -is [array]) { return '@(' + (($v | ForEach-Object { Format-Value $_ }) -join ', ') + ')' }
    "'$v' [$($v.GetType().Name)]"
}

function Assert-Equal($Expected, $Actual) {
    $ok = $false
    if ($Expected -is [array] -or $Actual -is [array]) {
        $e = @($Expected); $a = @($Actual)
        $ok = $e.Count -eq $a.Count
        for ($i = 0; $ok -and $i -lt $e.Count; $i++) { $ok = $e[$i] -eq $a[$i] }
    } elseif ($null -eq $Expected) {
        $ok = $null -eq $Actual
    } else {
        $ok = ($null -ne $Actual) -and ($Expected -eq $Actual)
    }
    if (-not $ok) { throw "expected $(Format-Value $Expected) but got $(Format-Value $Actual)" }
}

function Assert-True($Condition, [string]$What = 'condition') {
    if (-not $Condition) { throw "expected true: $What" }
}

function Assert-Match([string]$Pattern, [string]$Actual) {
    if ($Actual -notmatch $Pattern) { throw "expected '$Actual' to match '$Pattern'" }
}

function Complete-Tests {
    Write-Host ''
    Write-Host "$script:Passed passed, $($script:Failed.Count) failed"
    if ($script:Failed.Count) { exit 1 }
}
