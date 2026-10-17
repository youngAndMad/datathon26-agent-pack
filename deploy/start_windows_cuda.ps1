[CmdletBinding()]
param([switch]$Prepare, [switch]$Smoke)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Repo
$CpuPy = "$Repo\.venv-windows\Scripts\python.exe"
$GpuPy = "$Repo\.tools\venv-cuda\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $CpuPy)) { throw 'Existing .venv-windows is required to preserve Python and dependency versions.' }
if ($Prepare) {
    & $CpuPy "$Repo\scripts\windows_cuda_setup.py" --prepare
    if ($LASTEXITCODE -ne 0) { throw 'GPU environment preparation failed; existing CPU environment was preserved.' }
}
if (-not (Test-Path -LiteralPath $GpuPy)) { throw 'Missing isolated GPU environment. Run this script with -Prepare after updating the NVIDIA driver.' }
foreach ($Name in @('worker','api')) {
    if (Test-Path -LiteralPath "$Repo\data\windows-local\run\$Name.windows.json") {
        throw 'Stop the existing service with deploy\stop.ps1 -WindowsLocalTest before GPU launch.'
    }
}
& $CpuPy "$Repo\scripts\windows_model_2b.py" --verify-only
if ($LASTEXITCODE -ne 0) { throw 'Local 2B weights failed verification. Prepare them using scripts/windows_model_2b.py first.' }
$env:CASHWATCH_MAX_QUEUE_LENGTH = '0'
& "$PSScriptRoot\build_ui.ps1"
& "$PSScriptRoot\start.ps1" -Mode test -WindowsLocalTest -Device cuda -CudaNf4 -ModelSize 2b -TimeoutSeconds 700
if ($Smoke) {
    & $GpuPy "$Repo\scripts\windows_2b_smoke.py"
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic GPU analysis failed. See data/windows-local/2b-smoke-summary.json.' }
}
