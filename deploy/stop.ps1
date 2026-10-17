[CmdletBinding()]
param([string]$RunDirectory = '')
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $RunDirectory) { $RunDirectory = Join-Path $Repo 'data\run' }
foreach ($Name in @('api', 'worker')) {
    $Record = Join-Path $RunDirectory "$Name.windows.json"
    if (-not (Test-Path -LiteralPath $Record)) { continue }
    $Saved = Get-Content -LiteralPath $Record -Raw | ConvertFrom-Json
    $Process = Get-Process -Id $Saved.id -ErrorAction SilentlyContinue
    if ($Process) {
        if ($Process.StartTime.ToUniversalTime().Ticks.ToString() -ne $Saved.start_ticks) {
            throw "Refusing to stop reused PID for $Name. Review $Record."
        }
        # /T includes worker core and its local inference subprocesses.
        & taskkill.exe /PID $Saved.id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $Saved.id -ErrorAction SilentlyContinue)) {
            throw "Could not stop $Name"
        }
    }
    Remove-Item -LiteralPath $Record
}
Write-Host 'Tracked Cashwatch processes stopped.'
