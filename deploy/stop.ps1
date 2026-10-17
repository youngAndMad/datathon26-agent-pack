[CmdletBinding()]
param([string]$RunDirectory = '', [switch]$WindowsLocalTest)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $RunDirectory) {
    $RunDirectory = Join-Path $Repo $(if ($WindowsLocalTest) { 'data\windows-local\run' } else { 'data\run' })
}
$Failures = @()
foreach ($Name in @('api', 'worker')) {
    try {
    $Record = Join-Path $RunDirectory "$Name.windows.json"
    if (-not (Test-Path -LiteralPath $Record)) { continue }
    $Saved = Get-Content -LiteralPath $Record -Raw | ConvertFrom-Json
    $Process = Get-Process -Id $Saved.id -ErrorAction SilentlyContinue
    if ($Process) {
        if ($Process.StartTime.ToUniversalTime().Ticks.ToString() -ne $Saved.start_ticks) {
            throw "Refusing to stop reused PID for $Name. Review $Record."
        }
        # /T includes worker core and its local inference subprocesses.
        $TreeStopped = $false
        try {
            & taskkill.exe /PID $Saved.id /T /F | Out-Null
            $TreeStopped = ($LASTEXITCODE -eq 0)
        } catch { Write-Warning "taskkill unavailable for $Name; trying PowerShell process cleanup." }
        if (-not $TreeStopped) {
            # Preserve identity for every descendant; stop leaves before their supervisor.
            # A CIM failure must retain the launch record rather than claim full cleanup.
            try { $Snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop) }
            catch {
                $Current = Get-Process -Id $Saved.id -ErrorAction SilentlyContinue
                if ($Current) {
                    if ($Current.StartTime.ToUniversalTime().Ticks.ToString() -ne $Saved.start_ticks) {
                        throw "Refusing to stop reused PID for $Name."
                    }
                    Stop-Process -InputObject $Current -Force -ErrorAction Stop
                }
                throw "Stopped tracked $Name process, but descendant enumeration was denied. Child cleanup is unverified; launch record retained."
            }
            $Pending = @(@{ id = [int]$Saved.id; ticks = [long]$Saved.start_ticks })
            $Tree = @()
            $Seen = @{}
            while ($Pending.Count -gt 0) {
                $Node = $Pending[0]
                $Pending = @($Pending | Select-Object -Skip 1)
                if ($Seen.ContainsKey($Node.id)) { continue }
                $Seen[$Node.id] = $true
                $Tree += $Node
                foreach ($Child in @($Snapshot | Where-Object { $_.ParentProcessId -eq $Node.id })) {
                    $ChildProcess = Get-Process -Id $Child.ProcessId -ErrorAction SilentlyContinue
                    if (-not $ChildProcess) { continue }
                    $ChildStart = $ChildProcess.StartTime.ToUniversalTime()
                    if ($ChildStart.Ticks -lt $Node.ticks) { continue }
                    if ([Math]::Abs(($ChildStart - $Child.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 10) { continue }
                    $Pending += @{ id = [int]$Child.ProcessId; ticks = $ChildStart.Ticks }
                }
            }
            for ($Index = $Tree.Count - 1; $Index -ge 0; $Index--) {
                $Node = $Tree[$Index]
                $Current = Get-Process -Id $Node.id -ErrorAction SilentlyContinue
                if (-not $Current) { continue }
                if ($Current.StartTime.ToUniversalTime().Ticks -ne $Node.ticks) {
                    throw "Refusing to stop reused descendant PID $($Node.id) for $Name."
                }
                Stop-Process -InputObject $Current -Force -ErrorAction Stop
            }
        }
    }
    Remove-Item -LiteralPath $Record
    } catch { $Failures += "$Name cleanup failed: $($_.Exception.Message)" }
}
if ($Failures.Count) { throw ($Failures -join '; ') }
Write-Host 'Tracked Cashwatch processes stopped.'
