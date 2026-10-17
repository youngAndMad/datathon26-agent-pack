[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
# Process-local CPU tuning; shared configuration and package versions are unchanged.
$env:OMP_NUM_THREADS = '4'
$env:MKL_NUM_THREADS = '4'
& "$PSScriptRoot\build_ui.ps1"
& "$PSScriptRoot\start.ps1" -Mode test -WindowsLocalTest -LowMemory -TimeoutSeconds 700
