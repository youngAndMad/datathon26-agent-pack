[CmdletBinding()]
param(
    [ValidateSet('production', 'test')][string]$Mode = 'production',
    [ValidateSet('transformers', 'fake')][string]$Backend = 'transformers',
    [ValidateSet('cpu', 'cuda')][string]$Device = 'cpu',
    [ValidateRange(1,65535)][int]$Port = 8000,
    [ValidateRange(1,1800)][int]$TimeoutSeconds = 240
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($Mode -eq 'production' -and $Backend -eq 'fake') { throw 'Fake backend is forbidden in production.' }
if ($Mode -eq 'test') { Write-Warning 'TEST MODE: synthetic rules only; results do not establish banking violations.' }
if ($Backend -eq 'fake') { Write-Warning 'FAKE BACKEND: plumbing check only; no real model inference.' }
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Repo
$Py = "$Repo\.venv-windows\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) { throw 'Missing .venv-windows. Run deploy\setup.ps1 first.' }
$env:CASHWATCH_MODE = $Mode
$env:CASHWATCH_BACKEND = $Backend
$env:CASHWATCH_REGISTRY_PATH = if ($Mode -eq 'test') { "$Repo\rules\registry.test.yaml" } else { "$Repo\rules\registry.yaml" }
$env:CASHWATCH_DEVICE = $Device
$env:CASHWATCH_HOST = '127.0.0.1'
$env:CASHWATCH_PORT = $Port.ToString()
$env:CASHWATCH_DATA_DIR = "$Repo\data"
$env:CASHWATCH_API_KEY_FILE = "$Repo\secrets\jury_api_key"
$env:CASHWATCH_MODEL_PATH = "$Repo\models\qwen3-vl-4b-instruct"
$env:CASHWATCH_MODEL_MANIFEST = "$Repo\models\manifest.json"
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:DO_NOT_TRACK = '1'
$env:PYTHONUNBUFFERED = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
foreach ($Name in @('OPENAI_API_KEY','ANTHROPIC_API_KEY','GOOGLE_API_KEY','GEMINI_API_KEY','HF_TOKEN','HUGGING_FACE_HUB_TOKEN','AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AZURE_OPENAI_API_KEY')) {
    [Environment]::SetEnvironmentVariable($Name, $null, 'Process')
}
if (Test-Path -LiteralPath "$Repo\.tools\ffmpeg\bin") { $env:PATH = "$Repo\.tools\ffmpeg\bin;$env:PATH" }
foreach ($Tool in @('ffmpeg','ffprobe')) {
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) { throw "Missing $Tool" }
}
$RequiredArtifacts = @($env:CASHWATCH_API_KEY_FILE, $env:CASHWATCH_REGISTRY_PATH, "$Repo\frontend\dist\index.html")
if ($Backend -ne 'fake') { $RequiredArtifacts += @($env:CASHWATCH_MODEL_MANIFEST, "$env:CASHWATCH_MODEL_PATH\config.json") }
foreach ($Required in $RequiredArtifacts) {
    if (-not (Test-Path -LiteralPath $Required)) { throw "Missing required local artifact: $Required" }
}
# Fail fast on unfinished wiring / unapproved rules rather than running an endless restart loop.
& $Py -c "from cashwatch.settings import get_settings; from cashwatch.reporting import create_reporting; from cashwatch.rules import create_rules,load_registry; s=get_settings(); s.read_api_key(); assert not s.production_errors(), 'Invalid production configuration'; r=load_registry(s.registry_path,require_approved=True,allow_test=s.mode=='test'); assert r.approved_rule_ids() and not r.health_errors(), 'No healthy approved rule registry'"
if ($LASTEXITCODE -ne 0) { throw "$Mode preflight failed: required modules or valid rule registry are unavailable. No service was started." }
$Run = "$Repo\data\run"
$Logs = "$Repo\data\logs"
New-Item -ItemType Directory -Force -Path $Run,$Logs | Out-Null
foreach ($Name in @('worker','api')) {
    if (Test-Path -LiteralPath "$Run\$Name.windows.json") { throw 'Existing launch record. Run deploy\stop.ps1 first.' }
}
$Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $Listener.Start() } finally { $Listener.Stop() }
try {
    foreach ($Name in @('worker','api')) {
        $Module = if ($Name -eq 'worker') { 'cashwatch.pipeline.worker' } else { 'cashwatch.api.main' }
        $Process = Start-Process -FilePath $Py -ArgumentList @('-m',$Module) -WorkingDirectory $Repo -WindowStyle Hidden -PassThru -RedirectStandardOutput "$Logs\$Name.stdout.log" -RedirectStandardError "$Logs\$Name.stderr.log"
        @{ id = $Process.Id; start_ticks = $Process.StartTime.ToUniversalTime().Ticks.ToString() } | ConvertTo-Json | Set-Content -LiteralPath "$Run\$Name.windows.json"
    }
    $Timer = [Diagnostics.Stopwatch]::StartNew()
    $Ready = $false
    try {
        while ($Timer.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
            foreach ($Name in @('worker','api')) {
                $Saved = Get-Content -LiteralPath "$Run\$Name.windows.json" -Raw | ConvertFrom-Json
                if (-not (Get-Process -Id $Saved.id -ErrorAction SilentlyContinue)) { throw "$Name exited; see local logs in $Logs" }
            }
            try {
                $Request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/readyz")
                $Request.Proxy = $null
                $Request.Timeout = 2000
                $Response = $Request.GetResponse()
                try { $Ready = ([int]$Response.StatusCode -eq 200) } finally { $Response.Close() }
            } catch [System.Net.WebException] { if ($_.Exception.Response) { $_.Exception.Response.Close() } }
            if ($Ready) { break }
            Start-Sleep -Seconds 1
        }
    } finally { $Timer.Stop() }
    if (-not $Ready) { throw "Readiness timeout after $TimeoutSeconds seconds. See $Logs. CPU performance has not been qualified for the five-minute limit." }
    Write-Host "Ready: http://127.0.0.1:$Port ($Mode, $Backend, $Device)"
    Write-Host "Logs: $Logs. Stop: powershell -NoProfile -ExecutionPolicy Bypass -File deploy\stop.ps1"
} catch {
    & "$PSScriptRoot\stop.ps1" -RunDirectory $Run
    throw
}
