[CmdletBinding()]
param(
    [ValidateSet('bfloat16', 'float32')][string]$Precision = 'bfloat16',
    [switch]$CpuInt8
)
$ErrorActionPreference = 'Stop'
# Process-local CPU tuning; shared configuration and package versions are unchanged.
$env:OMP_NUM_THREADS = '4'
$env:MKL_NUM_THREADS = '4'
# The upstream queue was measured on an accelerator, not this CPU/disk profile.
$env:CASHWATCH_MAX_QUEUE_LENGTH = '0'
& "$PSScriptRoot\build_ui.ps1"
if ($CpuInt8) {
    & "$PSScriptRoot\start.ps1" -Mode test -WindowsLocalTest -CpuInt8 -TimeoutSeconds 700
} else {
    & "$PSScriptRoot\start.ps1" -Mode test -WindowsLocalTest -LowMemory -LowMemoryDtype $Precision -TimeoutSeconds 700
}
