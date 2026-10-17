[CmdletBinding()]
param(
    [string]$Python = 'py',
    [switch]$DownloadModel,
    [switch]$DownloadFfmpeg,
    [switch]$SkipFrontend
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Repo
function Check-Exit([string]$Step) { if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE). Preparation is incomplete." } }
$Py = "$Repo\.venv-windows\Scripts\python.exe"
$Bootstrap = "$Repo\.tools\python-bootstrap"
if (-not (Test-Path -LiteralPath "$Bootstrap\uv\__init__.py")) {
    if ($Python -eq 'py') { & $Python -3.11 -m pip install --target $Bootstrap 'uv==0.10.12' }
    else { & $Python -m pip install --target $Bootstrap 'uv==0.10.12' }
    Check-Exit 'Bootstrapping local uv'
}
if (-not (Test-Path -LiteralPath $Py)) {
    $PreviousPythonPath = $env:PYTHONPATH
    $PreviousPythonInstall = $env:UV_PYTHON_INSTALL_DIR
    $PreviousDownloadCache = $env:UV_CACHE_DIR
    $env:PYTHONPATH = $Bootstrap
    $env:UV_PYTHON_INSTALL_DIR = "$Repo\.tools\python"
    $env:UV_CACHE_DIR = "$Repo\.tools\uv-cache"
    try {
        if ($Python -eq 'py') { & $Python -3.11 -m uv python install 3.12 }
        else { & $Python -m uv python install 3.12 }
        Check-Exit 'Installing managed Python 3.12'
        if ($Python -eq 'py') { & $Python -3.11 -m uv venv --python 3.12 --seed .venv-windows }
        else { & $Python -m uv venv --python 3.12 --seed .venv-windows }
        Check-Exit 'Creating Python 3.12 runtime environment'
    } finally {
        $env:PYTHONPATH = $PreviousPythonPath
        $env:UV_PYTHON_INSTALL_DIR = $PreviousPythonInstall
        $env:UV_CACHE_DIR = $PreviousDownloadCache
    }
}
& $Py -c "import sys; assert sys.version_info[:2] == (3,12), 'Runtime requires Python 3.12; existing venv was not modified'"
Check-Exit 'Checking runtime Python version'
$PreviousPythonPath = $env:PYTHONPATH
$PreviousUvEnvironment = $env:UV_PROJECT_ENVIRONMENT
$PreviousUvCache = $env:UV_CACHE_DIR
$PreviousPythonInstall = $env:UV_PYTHON_INSTALL_DIR
$env:PYTHONPATH = $Bootstrap
$env:UV_PROJECT_ENVIRONMENT = "$Repo\.venv-windows"
$env:UV_CACHE_DIR = "$Repo\.tools\uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$Repo\.tools\python"
try {
    if ($Python -eq 'py') { & $Python -3.11 -m uv sync --frozen --extra torch }
    else { & $Python -m uv sync --frozen --extra torch }
    Check-Exit 'Installing locked Python dependencies'
} finally {
    $env:PYTHONPATH = $PreviousPythonPath
    $env:UV_PROJECT_ENVIRONMENT = $PreviousUvEnvironment
    $env:UV_CACHE_DIR = $PreviousUvCache
    $env:UV_PYTHON_INSTALL_DIR = $PreviousPythonInstall
}
if (-not $SkipFrontend) {
    Push-Location -LiteralPath "$Repo\frontend"
    try {
        & npm.cmd ci
        Check-Exit 'Installing frontend dependencies'
        & npm.cmd run build
        Check-Exit 'Building frontend'
    } finally { Pop-Location }
}
if ($DownloadModel) {
    & $Py scripts/model_fetch.py
    Check-Exit 'Downloading model snapshot'
}
$SecretDir = "$Repo\secrets"
New-Item -ItemType Directory -Force -Path $SecretDir | Out-Null
$KeyFile = Join-Path $SecretDir 'jury_api_key'
if (-not (Test-Path -LiteralPath $KeyFile)) {
    & $Py -c "import pathlib,secrets; pathlib.Path('secrets/jury_api_key').write_text(secrets.token_urlsafe(48),encoding='utf-8')"
    Check-Exit 'Generating API key file'
    $Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $KeyFile /inheritance:r /grant:r "${Identity}:(F)" 'SYSTEM:(F)' | Out-Null
    Check-Exit 'Restricting API key file permissions'
}
$LocalBin = "$Repo\.tools\ffmpeg\bin"
if ($DownloadFfmpeg -and -not (Test-Path -LiteralPath "$LocalBin\ffprobe.exe")) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ArchiveUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
    $Checksum = (Invoke-WebRequest -UseBasicParsing -Uri "$ArchiveUrl.sha256").Content
    if ($Checksum -notmatch '(?i)\b[0-9a-f]{64}\b') { throw 'FFmpeg provider returned an invalid checksum.' }
    $ExpectedHash = $Matches[0].ToLowerInvariant()
    $Stage = Join-Path $Repo ('.tools\ffmpeg-stage-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $Stage | Out-Null
    $Archive = Join-Path $Stage 'ffmpeg.zip'
    Invoke-WebRequest -UseBasicParsing -Uri $ArchiveUrl -OutFile $Archive
    if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) {
        throw 'FFmpeg checksum mismatch. Archive was not extracted.'
    }
    Expand-Archive -LiteralPath $Archive -DestinationPath (Join-Path $Stage 'extracted')
    $Probe = @(Get-ChildItem -LiteralPath (Join-Path $Stage 'extracted') -Filter ffprobe.exe -Recurse)
    if ($Probe.Count -ne 1) { throw 'Unexpected FFmpeg archive structure.' }
    New-Item -ItemType Directory -Force -Path $LocalBin | Out-Null
    foreach ($Binary in @('ffmpeg.exe','ffprobe.exe')) {
        Copy-Item -LiteralPath (Join-Path $Probe[0].DirectoryName $Binary) -Destination $LocalBin
    }
    @{ source = $ArchiveUrl; sha256 = $ExpectedHash; verified_at = [DateTime]::UtcNow.ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath "$Repo\.tools\ffmpeg\download.json"
    # Keep source archive and license material for audit/offline redistribution.
}
if (Test-Path -LiteralPath $LocalBin) { $env:PATH = "$LocalBin;$env:PATH" }
foreach ($Tool in @('ffmpeg', 'ffprobe')) {
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) {
        throw "Missing $Tool. Install FFmpeg and FFprobe on PATH or put both in .tools\ffmpeg\bin."
    }
}
& $Py -c "from cashwatch.settings import get_settings; from cashwatch.reporting import create_reporting; from cashwatch.rules import create_rules,load_registry; s=get_settings(); r=load_registry(s.registry_path,require_approved=True,allow_test=False); assert r.approved_rule_ids() and not r.health_errors(), 'Approved rules unavailable'"
Check-Exit 'Production module and rule preflight (dependencies may be installed but implementation is incomplete)'
Write-Host 'Dependencies prepared. Start with: powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start.ps1'
Write-Host 'Installation is not proof of inference quality or production readiness.'
