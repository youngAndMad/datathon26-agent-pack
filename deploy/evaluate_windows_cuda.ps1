[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Manifest,
    [ValidateRange(1,100000)][int]$ExpectedClips = 49,
    [string]$Rules = 'CW11,CW12',
    [string]$RunName = '',
    [string]$Profile = '',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Repo
$Py = "$Repo\.tools\venv-cuda\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) { throw 'Prepare the isolated CUDA environment first.' }
if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) { throw 'Manifest not found. Build it locally from reviewed labels first.' }
if (-not $CheckOnly) {
    foreach ($Name in @('worker','api')) {
        foreach ($Storage in @('data','data\windows-local')) {
            if (Test-Path -LiteralPath "$Repo\$Storage\run\$Name.windows.json") {
                throw 'Stop the application before evaluation so two models do not share GPU memory. Use deploy/stop.ps1 with the matching storage option.'
            }
        }
    }
    & $Py "$Repo\scripts\windows_cuda_probe.py"
    if ($LASTEXITCODE -ne 0) { throw 'CUDA preflight failed.' }
    & $Py "$Repo\scripts\windows_model_2b.py" --verify-only
    if ($LASTEXITCODE -ne 0) { throw 'Local 2B weights failed verification.' }
}
$env:CASHWATCH_MODE = 'test'
$env:CASHWATCH_BACKEND = 'transformers'
$env:CASHWATCH_DEVICE = 'cuda'
$env:CASHWATCH_WINDOWS_CUDA_NF4 = '1'
$env:CASHWATCH_WINDOWS_CPU_INT8 = '0'
$env:CASHWATCH_WINDOWS_CPU_LOW_MEMORY = '0'
$env:CASHWATCH_MODEL_PATH = "$Repo\models\qwen3-vl-2b-instruct"
$env:CASHWATCH_MODEL_MANIFEST = "$Repo\models\manifest.windows-2b.json"
$env:CASHWATCH_REGISTRY_PATH = "$Repo\rules\registry.yaml"
$env:CASHWATCH_PROFILE_PATH = if ($Profile) { (Resolve-Path -LiteralPath $Profile).Path } else { "$Repo\configs\runtime-profile.json" }
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONUTF8 = '1'
if (Test-Path -LiteralPath "$Repo\.tools\ffmpeg\bin") { $env:PATH = "$Repo\.tools\ffmpeg\bin;$env:PATH" }
$EvalArgs = @("$Repo\scripts\dev_eval.py", '--manifest', $Manifest, '--expected-clips', $ExpectedClips.ToString(), '--rules', $Rules, '--registry', "$Repo\rules\registry.yaml")
if ($RunName) { $EvalArgs += @('--run', $RunName) }
if ($CheckOnly) { $EvalArgs += '--check-selection' }
Write-Host 'LOCAL DEVELOPMENT EVALUATION: bank rule candidates, real model; not an independent holdout or production approval.'
& $Py @EvalArgs
if ($LASTEXITCODE -ne 0) { throw 'Evaluation failed. Inspect local errors; do not interpret missing results as clean videos.' }
if (-not $CheckOnly) { Write-Host 'Reports: data/dev_eval/<run>/summary.json. Check completed/failed counts even when the script exits successfully.' }
