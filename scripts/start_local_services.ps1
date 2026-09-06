param(
    [string]$RuntimeDb = "data\runtime\pra_runtime.sqlite3"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LocalEnvPath = Join-Path $PSScriptRoot "local_env.ps1"

Set-Location $ProjectRoot
$env:PYTHONIOENCODING = "utf-8"

$PythonExe = (& python -c "import sys; print(sys.executable)").Trim()
if (-not $PythonExe) {
    throw "Unable to resolve the active Python interpreter."
}

if (Test-Path $LocalEnvPath) {
    . $LocalEnvPath
}

& $PythonExe scripts/run_shadowbot_queue_services.py --runtime-db $RuntimeDb
exit $LASTEXITCODE
