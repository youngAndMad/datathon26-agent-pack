[CmdletBinding()]
param([string]$RunDirectory = '', [switch]$WindowsLocalTest)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $RunDirectory) {
    $RunDirectory = Join-Path $Repo $(if ($WindowsLocalTest) { 'data\windows-local\run' } else { 'data\run' })
}
$Python = Join-Path $Repo '.venv-windows\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = Join-Path $Repo '.venv\Scripts\python.exe'
}
$Helper = Join-Path $Repo 'scripts\windows_stop_tree.py'
$Failures = @()
foreach ($Name in @('api', 'worker')) {
    try {
        $Record = Join-Path $RunDirectory "$Name.windows.json"
        if (-not (Test-Path -LiteralPath $Record)) { continue }
        # Snapshot and suspend the full identity-verified tree BEFORE killing roots.
        # Native APIs avoid CIM/taskkill restrictions that previously stranded workers.
        if (-not (Test-Path -LiteralPath $Python)) { throw 'Windows Python environment not found.' }
        & $Python $Helper --record $Record
        if ($LASTEXITCODE -ne 0) { throw 'Descendant cleanup unverified; launch record retained.' }
        Remove-Item -LiteralPath $Record
    } catch { $Failures += "$Name cleanup failed: $($_.Exception.Message)" }
}
if ($Failures.Count) { throw ($Failures -join '; ') }
Write-Host 'Tracked Cashwatch processes stopped.'
