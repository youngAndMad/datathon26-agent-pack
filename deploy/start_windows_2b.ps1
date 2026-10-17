[CmdletBinding()]
param([switch]$Prepare, [switch]$Smoke)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Repo
$Py = "$Repo\.venv-windows\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) { throw 'Missing existing .venv-windows. No packages were changed.' }
foreach ($Name in @('worker','api')) {
    if (Test-Path -LiteralPath "$Repo\data\windows-local\run\$Name.windows.json") {
        throw 'Existing service launch record. Stop it with deploy\stop.ps1 -WindowsLocalTest before changing models.'
    }
}
if ($Prepare) {
    & $Py "$Repo\scripts\windows_model_2b.py"
    if ($LASTEXITCODE -ne 0) { throw '2B preparation failed. Service was not started; downloaded partial files can be resumed.' }
} else {
    & $Py "$Repo\scripts\windows_model_2b.py" --verify-only
    if ($LASTEXITCODE -ne 0) { throw '2B weights are missing or invalid. Run this script once with -Prepare and Internet access.' }
}
$env:OMP_NUM_THREADS = '4'
$env:MKL_NUM_THREADS = '4'
$env:CASHWATCH_MAX_QUEUE_LENGTH = '0'
& "$PSScriptRoot\build_ui.ps1"
& "$PSScriptRoot\start.ps1" -Mode test -WindowsLocalTest -CpuInt8 -ModelSize 2b -TimeoutSeconds 700
if ($Smoke) {
    & $Py "$Repo\scripts\windows_2b_smoke.py"
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic 2B analysis did not pass. See data/windows-local/2b-smoke-summary.json; readiness alone is not analysis success.' }
}
