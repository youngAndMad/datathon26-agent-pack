[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $Repo 'frontend'
# The shared config is valid JavaScript. Native loading avoids the Windows
# config-bundler child-process failure without changing Vite/Node versions.
$TemporaryConfig = Join-Path $Frontend ('.vite-windows-' + [guid]::NewGuid().ToString('N') + '.mjs')
Push-Location -LiteralPath $Frontend
try {
    & npm.cmd run typecheck
    if ($LASTEXITCODE -ne 0) { throw 'Frontend typecheck failed.' }
    Copy-Item -LiteralPath (Join-Path $Frontend 'vite.config.ts') -Destination $TemporaryConfig
    & node.exe node_modules/vite/bin/vite.js build --config $TemporaryConfig --configLoader native
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally {
    if (Test-Path -LiteralPath $TemporaryConfig) { Remove-Item -LiteralPath $TemporaryConfig }
    Pop-Location
}
